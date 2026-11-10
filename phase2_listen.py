#!/usr/bin/env python3
"""Phase 2 Demo：唤醒 → 听你说 → 识别出文字

完整链路：

    麦克风 ──► KWS 待命监听
                 │ 命中「大肥鱼」
                 ▼
             说「我在」（本地 SAPI）
                 │
                 ▼
             进入对话态：VAD 断句
                 │ 你说完一句（停顿 >1 秒）
                 ▼
             云端流式 ASR 转写
                 │
                 ▼
             把文字打印出来（Phase 3 会接 LLM）

这一步之后，它就能听懂你说什么了。

用法：
    python phase2_listen.py
    python phase2_listen.py --timeout 15      # 唤醒后 15 秒没说话就退回待命
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from asr_aliyun import SAMPLE_RATE, AliyunASR  # noqa: E402
from audio_player import sapi_speak  # noqa: E402
from phase0_kws_poc import create_spotter, pick_input_device, resample_to_16k  # noqa: E402
from voice_config import load_keys  # noqa: E402

# ---------- 参数（参考 desk-ai 实测值） ----------

# VAD：能量迟滞
VAD_START_RMS = 0.03      # 起说话阈值
VAD_END_RMS = 0.012       # 收尾阈值（说话中允许更低，避免句中停顿被切）

# 静音切段：对话态用 1050ms
# desk-ai 教训：800ms 太短，长句换气容易被切成两段
SILENCE_FLUSH_MS = 1050

# 最短有效语音
MIN_SPEECH_MS = 400

# 单段上限：防止无限累积
MAX_SEGMENT_MS = 15000

# 唤醒后多久没说话退回待命
DEFAULT_DIALOGUE_TIMEOUT = 20.0


class VoiceLoop:
    """待命 → 唤醒 → 听写 → 回到待命的循环。"""

    def __init__(
        self,
        api_key: str,
        model_dir: Path,
        wake_reply: str = "我在",
        device: int | None = None,
        dialogue_timeout: float = DEFAULT_DIALOGUE_TIMEOUT,
        score: float = 3.0,
        threshold: float = 0.1,
    ):
        self.api_key = api_key
        self.wake_reply = wake_reply
        self.dialogue_timeout = dialogue_timeout

        # KWS
        self.spotter, self.kws_stream = create_spotter(
            model_dir, score=score, threshold=threshold
        )

        # 设备
        self.device, self.sample_rate, self.device_desc = pick_input_device(device)
        self.native_16k = self.sample_rate == 16000

        # 状态
        self.state = "idle"          # idle | awake
        self.awake_until = 0.0
        self.transcripts: list[str] = []

        # ASR（每句一个新会话）
        self.asr: AliyunASR | None = None

        # 录音缓冲
        self.seg_buf: list[np.ndarray] = []
        self.speaking = False
        self.silence_ms = 0.0
        self.seg_start = 0.0

    # ---------- 主循环 ----------

    def run(self) -> None:
        print("=== 唤醒 → 听写 Demo ===\n")
        print(f"  设备：{self.device_desc}")
        print(f"  采样率：{self.sample_rate} Hz"
              + ("" if self.native_16k else " → 重采样 16000"))
        print(f"  唤醒后 {self.dialogue_timeout:.0f} 秒内不说话会退回待命\n")
        print("说「大肥鱼」叫醒它，然后直接说话。Ctrl+C 退出。\n")

        block = int(self.sample_rate * 0.1)
        aq: queue.Queue = queue.Queue()

        def cb(indata, frames, ti, status):
            if status:
                print(f"  [音频] {status}", file=sys.stderr)
            aq.put(indata.copy())

        try:
            with sd.InputStream(samplerate=self.sample_rate, channels=1,
                                dtype="float32", blocksize=block,
                                device=self.device, callback=cb):
                while True:
                    frame = aq.get().flatten()
                    self._tick(frame)
        except KeyboardInterrupt:
            print(f"\n\n=== 共识别 {len(self.transcripts)} 句 ===")
            for i, t in enumerate(self.transcripts, 1):
                print(f"  {i}. {t}")

    def _tick(self, frame: np.ndarray) -> None:
        # 重采样到 16k
        if not self.native_16k:
            frame = np.array(
                resample_to_16k(frame.tolist(), self.sample_rate),
                dtype=np.float32,
            )

        rms = float(np.sqrt(np.mean(frame ** 2)))
        self._render_status(rms)

        if self.state == "idle":
            self._tick_idle(frame)
        else:
            self._tick_awake(frame, rms)

    # ---------- 待命态：只听唤醒词 ----------

    def _tick_idle(self, frame: np.ndarray) -> None:
        self.kws_stream.accept_waveform(SAMPLE_RATE, frame.tolist())
        hit = None
        while self.spotter.is_ready(self.kws_stream):
            self.spotter.decode_stream(self.kws_stream)
            r = self.spotter.get_result(self.kws_stream)
            if r:
                hit = r.strip()
                self.spotter.reset_stream(self.kws_stream)
                break

        if hit:
            self._enter_awake(hit)

    def _enter_awake(self, keyword: str) -> None:
        self.state = "awake"
        self.awake_until = time.time() + self.dialogue_timeout
        self.seg_buf = []
        self.speaking = False
        self.silence_ms = 0.0

        print(f"\n\n  🎯 唤醒：{keyword}")
        print(f"  🔊 「{self.wake_reply}」")
        sapi_speak(self.wake_reply)
        print("  👂 在听...\n")

        self._start_asr()

    # ---------- 对话态：VAD 断句 + ASR ----------

    def _tick_awake(self, frame: np.ndarray, rms: float) -> None:
        now = time.time()

        # 超时退回待命
        if not self.speaking and now > self.awake_until:
            print("\n  ⏱ 超时，退回待命\n")
            self._leave_awake()
            return

        if rms > VAD_START_RMS:
            if not self.speaking:
                self.speaking = True
                self.seg_start = now
                self.seg_buf = []
            self.silence_ms = 0.0
        elif self.speaking:
            # 迟滞：说话中用更低的阈值判断是否还在说
            if rms > VAD_END_RMS:
                self.silence_ms = 0.0
            else:
                self.silence_ms += 100  # 每帧 100ms

        if self.speaking:
            self.seg_buf.append(frame)
            self.awake_until = now + self.dialogue_timeout

            # ⭐ 边说边送 ASR —— 这样 partial 字幕能实时出来
            if self.asr is not None:
                pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
                try:
                    self.asr.feed(pcm)
                except Exception:
                    pass  # 送失败不影响录音，断句时会处理

            seg_ms = len(self.seg_buf) * 100

            # 静音够久 → 断句
            if self.silence_ms >= SILENCE_FLUSH_MS:
                self._flush_segment()
            # 段太长 → 强制切
            elif seg_ms >= MAX_SEGMENT_MS:
                print("\n  ✂️ 超长强制断句")
                self._flush_segment()

    def _flush_segment(self) -> None:
        seg_ms = len(self.seg_buf) * 100
        self.speaking = False
        self.silence_ms = 0.0
        self.seg_buf = []

        if seg_ms < MIN_SPEECH_MS:
            # 太短，丢掉这段（但 ASR 会话里已经有音频了，得重置）
            if self.asr is not None:
                try:
                    self.asr.close()
                except Exception:
                    pass
                self._start_asr()
            return

        # ⚠️ 先把当前 ASR 引用抓下来，再交给线程。
        #    否则线程里读 self.asr 时，可能已经被下一次断句换成新的了
        #    （desk-ai 踩过的坑：旧会话退出把新会话清掉，导致后续彻底不识别）。
        asr_ref = self.asr
        if asr_ref is None:
            return

        print(f"\n  📤 断句（{seg_ms}ms），等识别结果 ...")

        def worker():
            try:
                text = asr_ref.finish()
                text = (text or "").strip()
                if text:
                    self.transcripts.append(text)
                    print(f"\n  💬 你说：{text}\n")
                else:
                    print("\n  （没识别出内容）\n")
            except Exception as e:
                print(f"\n  ❌ 识别失败：{e}\n")
            finally:
                self._start_asr()

        # 后台跑，别阻塞录音
        threading.Thread(target=worker, daemon=True).start()

    def _start_asr(self) -> None:
        try:
            self.asr = AliyunASR(
                self.api_key,
                on_partial=lambda t: print(f"\r  ⏳ {t:<50}", end="", flush=True),
                on_final=lambda t: None,
            )
            self.asr.start()
        except Exception as e:
            print(f"\n  ❌ ASR 启动失败：{e}\n")
            self.asr = None

    def _leave_awake(self) -> None:
        self.state = "idle"
        self.speaking = False
        self.seg_buf = []
        if self.asr:
            try:
                self.asr.close()
            except Exception:
                pass
            self.asr = None

    # ---------- 界面 ----------

    def _render_status(self, rms: float) -> None:
        bar = "█" * int(min(rms * 200, 24))
        icon = "🔴" if self.speaking else ("👂" if self.state == "awake" else "💤")
        print(f"\r {icon} {rms:.3f} {bar:<24}", end="", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="唤醒 → 听写 Demo")
    p.add_argument("--model-dir", type=Path, default=HERE / "kws-model")
    p.add_argument("--reply", type=str, default="我在")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--timeout", type=float, default=DEFAULT_DIALOGUE_TIMEOUT,
                   help="唤醒后无语音的退回时间（秒）")
    p.add_argument("--score", type=float, default=3.0)
    p.add_argument("--threshold", type=float, default=0.1)
    args = p.parse_args()

    keys = load_keys()
    if not keys.qwen:
        print("❌ 没找到 qwen API Key")
        print("   在项目根目录建 apikey.txt，写一行：qwen:sk-xxx")
        return 1

    loop = VoiceLoop(
        api_key=keys.qwen,
        model_dir=args.model_dir,
        wake_reply=args.reply,
        device=args.device,
        dialogue_timeout=args.timeout,
        score=args.score,
        threshold=args.threshold,
    )
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
