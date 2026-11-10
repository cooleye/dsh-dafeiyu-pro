#!/usr/bin/env python3
"""阿里云 Paraformer 实时语音识别。

协议要点（全是 desk-ai 踩过的坑，别再踩一遍）：

1. `run-task` 和 `finish-task` 的 header **都必须带 `"streaming": "duplex"`**。
   少了 finish 那份，服务端**完全静默，一个字节都不回**，而且不报错 ——
   表现为 recv 超时。这是最费时间的坑。

2. **不能走系统代理**：代理会拦 WebSocket 握手（实测直接超时）。

3. **结果按句推送**：`sentence_end=true` 才是完整句，
   中间的是逐字增长的 partial。

4. **音频用 WebSocket 二进制帧发送**，不是 JSON + base64！
   发错了会报 `NO_VALID_AUDIO_ERROR`，而且不告诉你格式错了。

5. **每条指令都要带 `task_id`**（run-task / continue-task / finish-task 都一样），
   漏了会报 `Missing required parameter 'header.task_id'`。

6. **会话必须带 id**：一段话被切成多段时，旧会话的退出不能把新会话清掉
   （desk-ai 的致命 bug：识别完一段后继续说，彻底不识别）。

用法：
    python asr_aliyun.py --wav test.wav        # 用文件测
    python asr_aliyun.py --mic                 # 用麦克风测
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
MODEL = "paraformer-realtime-v2"
SAMPLE_RATE = 16000


class AliyunASR:
    """流式 ASR 客户端。

    用法：
        asr = AliyunASR(api_key, on_partial=..., on_final=...)
        asr.start()
        asr.feed(pcm_bytes)   # 反复调用
        text = asr.finish()   # 返回最终文本
    """

    def __init__(
        self,
        api_key: str,
        on_partial=None,
        on_final=None,
        on_error=None,
        model: str = MODEL,
        sample_rate: int = SAMPLE_RATE,
        timeout: float = 20.0,
    ):
        self.api_key = api_key
        self.model = model
        self.sample_rate = sample_rate
        self.timeout = timeout

        self.on_partial = on_partial or (lambda t: None)
        self.on_final = on_final or (lambda t: None)
        self.on_error = on_error or (lambda e: None)

        self._ws = None
        self._task_id = ""
        self._session_id = uuid.uuid4().hex[:16]
        self._finals: list[str] = []
        self._partial = ""
        self._done = threading.Event()
        self._error = None
        self._reader = None
        self._lock = threading.Lock()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """建连 + 发 run-task。"""
        import websocket

        headers = [f"Authorization: bearer {self.api_key}"]
        self._ws = websocket.create_connection(
            WS_URL,
            header=headers,
            timeout=self.timeout,
            # 3) 不走系统代理
            http_proxy_host=None,
        )

        self._task_id = uuid.uuid4().hex
        run_cmd = {
            "header": {
                "action": "run-task",
                "task_id": self._task_id,
                "streaming": "duplex",   # 1) 必须带
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "language_hints": ["zh", "en"],
                },
                "input": {},
            },
        }
        self._ws.send(json.dumps(run_cmd))

        # 等 task-started
        started = self._wait_event("task-started", timeout=15)
        if not started:
            raise RuntimeError(
                "run-task 没收到 task-started（检查 key、模型名、网络代理）"
            )

        # 起后台线程收结果
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def feed(self, pcm: bytes) -> None:
        """喂一段 16k 单声道 16-bit PCM。

        ⚠️ 关键：音频用 **WebSocket 二进制帧** 发送，
        不是 JSON + base64。发错了服务端会报
        NO_VALID_AUDIO_ERROR（而且不告诉你格式错了）。
        """
        if self._ws is None:
            raise RuntimeError("还没 start()")
        if not pcm:
            return
        self._ws.send_binary(pcm)

    def finish(self, wait: float = 20.0) -> str:
        """发 finish-task，等结果，返回最终文本。"""
        if self._ws is None:
            raise RuntimeError("还没 start()")

        finish_cmd = {
            "header": {
                "action": "finish-task",
                "task_id": self._task_id,
                "streaming": "duplex",   # 1) 这个也必须带！
            },
            "payload": {"input": {}},
        }
        self._ws.send(json.dumps(finish_cmd))

        self._done.wait(wait)

        with self._lock:
            text = "".join(self._finals).strip()

        self.close()
        if self._error:
            raise RuntimeError(f"ASR 失败：{self._error}")
        return text

    def close(self) -> None:
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass
        self._ws = None

    # ---------- 内部 ----------

    def _wait_event(self, name: str, timeout: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                raw = self._ws.recv()
            except Exception:
                return False
            if not raw:
                return False
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            ev = msg.get("header", {}).get("event", "")
            if ev == name:
                return True
            if ev == "task-failed":
                self._error = msg.get("header", {}).get("error_message", "unknown")
                return False
        return False

    def _read_loop(self) -> None:
        try:
            while True:
                raw = self._ws.recv()
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                header = msg.get("header", {})
                event = header.get("event", "")

                if event == "result-generated":
                    self._handle_result(msg)
                elif event == "task-finished":
                    self._done.set()
                    break
                elif event == "task-failed":
                    self._error = header.get("error_message", "unknown")
                    self._done.set()
                    break
        except Exception as e:
            if not self._done.is_set():
                self._error = str(e)
                self._done.set()

    def _handle_result(self, msg: dict) -> None:
        payload = msg.get("payload", {})
        output = payload.get("output", {})
        sentence = output.get("sentence") or {}

        text = (sentence.get("text") or "").strip()
        if not text:
            return

        is_end = sentence.get("sentence_end", False)

        if is_end:
            # 3) sentence_end=true 才是完整句
            with self._lock:
                self._finals.append(text)
                self._partial = ""
            self.on_final(text)
        else:
            with self._lock:
                self._partial = text
            self.on_partial(text)


# ---------- 测试用 ----------


def read_pcm16(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        ch = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"只支持 16-bit，实际 {width * 8}-bit")
    return data, rate


def resample_if_needed(pcm: bytes, rate: int) -> bytes:
    if rate == SAMPLE_RATE:
        return pcm
    import array
    import numpy as np
    a = array.array("h")
    a.frombytes(pcm)
    arr = np.array(a, dtype=np.float32)
    n_out = int(len(arr) * SAMPLE_RATE / rate)
    x_old = np.linspace(0, 1, len(arr), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    out = np.interp(x_new, x_old, arr)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def run_wav(path: Path, key: str) -> int:
    print(f"=== 识别文件：{path} ===\n")
    pcm, rate = read_pcm16(path)
    pcm = resample_if_needed(pcm, rate)
    dur = len(pcm) / 2 / SAMPLE_RATE
    print(f"音频：{dur:.2f} 秒 @ {SAMPLE_RATE}Hz\n")

    finals = []

    def on_partial(t):
        print(f"\r  [partial] {t:<50}", end="", flush=True)

    def on_final(t):
        finals.append(t)
        print(f"\r  [final]   {t:<50}")

    asr = AliyunASR(key, on_partial=on_partial, on_final=on_final,
                    on_error=lambda e: print(f"\n  [error] {e}"))

    t0 = time.time()
    try:
        asr.start()
        # 按 100ms 一块喂，模拟实时
        chunk = int(SAMPLE_RATE * 0.1) * 2
        for i in range(0, len(pcm), chunk):
            asr.feed(pcm[i:i + chunk])
        text = asr.finish()
    except Exception as e:
        print(f"\n❌ {e}")
        return 1

    elapsed = time.time() - t0
    print(f"\n=== 结果 ===")
    print(f"文本：{text or '(空)'}")
    print(f"耗时：{elapsed:.2f} 秒（音频 {dur:.2f} 秒）")
    return 0


def run_mic(key: str) -> int:
    """麦克风实时识别（VAD 断句用简单能量阈值）。"""
    import numpy as np
    import sounddevice as sd

    from phase0_kws_poc import pick_input_device, resample_to_16k

    print("=== 麦克风实时识别 ===\n")
    dev, rate, desc = pick_input_device()
    print(f"设备：{desc}")
    print(f"采样率：{rate} Hz" + ("" if rate == 16000 else " → 重采样 16000"))
    print("\n说话，停顿 1 秒自动切句。Ctrl+C 退出。\n")

    finals = []

    asr = AliyunASR(
        key,
        on_partial=lambda t: print(f"\r  {t:<60}", end="", flush=True),
        on_final=lambda t: (finals.append(t), print(f"\r  ✓ {t:<60}")),
        on_error=lambda e: print(f"\n  [error] {e}"),
    )
    asr.start()

    block = int(rate * 0.1)
    q: list = []
    import queue
    aq: queue.Queue = queue.Queue()

    def cb(indata, frames, ti, status):
        aq.put(indata.copy())

    VAD_START = 0.02
    SILENCE_SEC = 1.0
    speaking = False
    silence_samples = 0

    try:
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                            blocksize=block, device=dev, callback=cb):
            while True:
                frame = aq.get().flatten()
                if rate != 16000:
                    frame = np.array(resample_to_16k(frame.tolist(), rate),
                                     dtype=np.float32)

                rms = float(np.sqrt(np.mean(frame ** 2)))
                bar = "█" * int(min(rms * 300, 25))
                state = "🔴" if speaking else "  "
                print(f"\r {state} {rms:.3f} {bar:<25}", end="", flush=True)

                if rms > VAD_START:
                    speaking = True
                    silence_samples = 0
                elif speaking:
                    silence_samples += len(frame)

                if speaking:
                    pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
                    asr.feed(pcm)

                # 静音够久 → 断句
                if speaking and silence_samples > SILENCE_SEC * 16000:
                    print("\r  ... 断句", flush=True)
                    text = asr.finish()
                    if text:
                        finals.append(text)
                    speaking = False
                    silence_samples = 0
                    # 新会话
                    asr = AliyunASR(
                        key,
                        on_partial=lambda t: print(f"\r  {t:<60}", end="", flush=True),
                        on_final=lambda t: print(f"\r  ✓ {t:<60}"),
                        on_error=lambda e: print(f"\n  [error] {e}"),
                    )
                    asr.start()
    except KeyboardInterrupt:
        print(f"\n\n=== 共识别 {len(finals)} 句 ===")
        for i, t in enumerate(finals, 1):
            print(f"  {i}. {t}")
    finally:
        asr.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="阿里云 Paraformer 流式 ASR")
    p.add_argument("--wav", type=Path, help="识别 WAV 文件")
    p.add_argument("--mic", action="store_true", help="麦克风实时识别")
    p.add_argument("--key", type=str, help="API Key（默认从配置读）")
    args = p.parse_args()

    key = args.key
    if not key:
        from voice_config import load_keys
        k = load_keys()
        key = k.qwen
        if not key:
            print("❌ 没找到 qwen key，用 --key 传入或用 voice_config.py 配置")
            return 1

    if args.wav:
        return run_wav(args.wav, key)
    if args.mic:
        return run_mic(key)

    print("用 --wav 或 --mic")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
