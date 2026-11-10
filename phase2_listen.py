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
from phase0_kws_poc import (  # noqa: E402
    create_spotter,
    input_candidates,
    pick_input_device,
    resample_to_16k,
)
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
        on_transcript=None,
        audio_mode: str = "shared",
    ):
        self.api_key = api_key
        self.wake_reply = wake_reply
        self.dialogue_timeout = dialogue_timeout
        # 识别出一句就回调（Phase 3 用它接 LLM）
        self.on_transcript = on_transcript
        # 每帧上报音量（用于「它说话时你一开口就打断」）
        # 注意：这不是 VAD，是「立即反应」——不等识别完
        self.on_voice_level = None

        # KWS
        self.spotter, self.kws_stream = create_spotter(
            model_dir, score=score, threshold=threshold
        )

        # 设备：shared=共享（和 DSH 语音输入共存）/ exclusive=WDM-KS（独占）
        # 存候选列表，开流失败时逐个回退（比预试开可靠）
        self.audio_candidates = input_candidates(device, mode=audio_mode)
        self.device, self.sample_rate, self.device_desc = self.audio_candidates[0]
        self.native_16k = self.sample_rate == 16000

        # 状态
        self.state = "idle"          # idle | awake
        self.awake_until = 0.0
        self.transcripts: list[str] = []

        # ASR（每段语音一个独立会话）
        self.asr: AliyunASR | None = None

        # 等 ASR 建连期间缓存的音频（重采样后的 16k PCM bytes）
        # 不缓存的话，每段开始的头几百毫秒会丢字
        self.pending_pcm: list[bytes] = []

        # 会话代数：每开一段 +1。建连是异步的，完成时如果代数变了，
        # 说明这段已经翻篇了，新连接直接丢掉，避免连接泄漏。
        self.asr_gen = 0

        # 建连失败退避：连续失败时拉长间隔，避免把服务打爆
        self._asr_fail_count = 0
        self._asr_cooldown_until = 0.0

        # 攒够几帧才建连（避免咳一声就开一条 WebSocket）
        self.min_frames_before_connect = 3
        # 本段是否已经发起过建连（防同一段重复建）
        self.pending_pcm_started = False

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

        # 逐个候选试开：开流失败就换下一个后端
        # （check_input_settings 只校验格式，不代表现在能打开）
        #
        # ⚠️ sounddevice 抛的是 PortAudioError，它直接继承 Exception，
        #    **不是** OSError —— 一开始写 `except OSError` 完全没接住，
        #    回退逻辑形同虚设，报错直接冲到最外面。
        last_err = None
        opened = False
        # 走两轮：设备刚被别的程序释放时，第一轮可能全失败，等一下再试
        for attempt in range(2):
            for idx, (dev, rate, desc) in enumerate(self.audio_candidates):
                self.device, self.sample_rate, self.device_desc = dev, rate, desc
                self.native_16k = rate == 16000
                try:
                    self._run_stream()
                    opened = True
                    break
                except (sd.PortAudioError, OSError) as e:
                    last_err = e
                    if idx < len(self.audio_candidates) - 1:
                        print(f"\n  ⚠️ {desc} 打不开（{str(e)[:60]}），换下一个后端 …")
                        time.sleep(0.3)
            if opened:
                return
            if attempt == 0:
                print("\n  ⏳ 所有后端都失败，1.5 秒后重试（设备可能还在释放）…")
                time.sleep(1.5)

        print(f"\n\n  ❌ 所有音频后端都打不开：{last_err}")
        print("     多半是被别的程序占着了。检查：")
        print("       · 终端里是不是还开着 phase4_assistant.py / phase2_listen.py")
        print("       · 桌宠插件是不是已经启用了语音")
        print("       · 微信 / QQ 语音、浏览器录音标签页")
        print("     共享模式（WASAPI/DirectSound/MME）可以和别人共存，")
        print("     独占模式（WDM-KS）不行。用 --audio-mode shared 试试。")
        raise SystemExit(1)

    def _run_stream(self) -> None:
        """用当前 self.device 跑主循环。"""
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

        # ⭐ 每帧上报音量，给「打断」用。
        #    打断必须在这一层做：等 ASR 出结果再停，要慢 1~2 秒，
        #    体验上就是「打断不灵」。
        if self.on_voice_level:
            try:
                self.on_voice_level(rms)
            except Exception:
                pass

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
        self.asr = None
        self.pending_pcm = []
        self.pending_pcm_started = False
        self.asr_gen += 1

        print(f"\n\n  🎯 唤醒：{keyword}")
        print(f"  🔊 「{self.wake_reply}」")
        sapi_speak(self.wake_reply)
        print("  👂 在听...\n")

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

            # ⭐ 攒够几帧再建连：
            #    咳一声、碰一下麦克风都会触发 speaking，如果立刻建 WebSocket，
            #    短促噪声多了会把连接打满（实测报 timed out）。
            if (self.asr is None
                    and not self.pending_pcm_started
                    and len(self.seg_buf) >= self.min_frames_before_connect
                    and time.time() >= self._asr_cooldown_until):
                self.pending_pcm_started = True
                self._start_asr_async()

            # ⭐ 边说边送 ASR —— 这样 partial 字幕能实时出来
            pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
            asr_now = self.asr
            if asr_now is not None:
                try:
                    asr_now.feed(pcm)
                except Exception:
                    # 会话刚被关掉（上一段收尾中）→ 存起来，等新会话补发
                    self.pending_pcm.append(pcm)
            else:
                # 还在建连 → 先缓存
                self.pending_pcm.append(pcm)

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
        self.pending_pcm_started = False

        if seg_ms < MIN_SPEECH_MS:
            # 太短（咳嗽、碰麦）→ 丢掉这段
            asr_stale = self.asr
            self.asr = None
            self.asr_gen += 1
            self.pending_pcm = []
            if asr_stale is not None:
                threading.Thread(target=asr_stale.close, daemon=True).start()
            return

        # ⚠️ 立刻脱钩！这样后续音频不会再喂进这个即将关闭的会话。
        #    之前没脱钩的后果：新一段音频进了已关闭的会话，
        #    下次断句时 finish() 报「还没 start()」。
        asr_ref = self.asr
        self.asr = None
        my_gen = self.asr_gen
        pending = self.pending_pcm
        self.pending_pcm = []

        # ⭐ 建连可能还在路上（慢网络），给一小段等待时间，
        #    否则「唤醒后的第一段话」会被直接丢掉。
        if asr_ref is None and pending:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if self.asr is not None and self.asr_gen == my_gen:
                    asr_ref = self.asr
                    self.asr = None
                    break
                if self.asr_gen != my_gen:
                    break          # 已经被新的一段接管了
                time.sleep(0.02)

        if asr_ref is None:
            print("\n  ⚠️ ASR 还没就绪，这段跳过\n")
            return

        print(f"\n  📤 断句（{seg_ms}ms），等识别结果 ...")

        def worker():
            try:
                text = asr_ref.finish()
                text = (text or "").strip()
                if text:
                    self.transcripts.append(text)
                    print(f"\n  💬 你说：{text}\n")
                    if self.on_transcript:
                        try:
                            self.on_transcript(text)
                        except Exception as e:
                            print(f"\n  ❌ on_transcript 回调失败：{e}\n")
                else:
                    print("\n  （没识别出内容）\n")
            except Exception as e:
                print(f"\n  ❌ 识别失败：{e}\n")

        # 后台跑，别阻塞录音
        threading.Thread(target=worker, daemon=True).start()

    def _start_asr_async(self) -> None:
        """后台建 ASR 会话，建好把缓存的音频补发进去。"""
        self.asr_gen += 1
        gen = self.asr_gen

        def run():
            try:
                asr = AliyunASR(
                    self.api_key,
                    on_partial=lambda t: print(f"\r  ⏳ {t:<50}", end="", flush=True),
                    on_final=lambda t: None,
                )
                asr.start()
            except Exception as e:
                # 失败退避：连续失败时逐步拉长冷却，避免把服务打爆
                self._asr_fail_count += 1
                backoff = min(30.0, 2.0 * self._asr_fail_count)
                self._asr_cooldown_until = time.time() + backoff
                print(f"\n  ❌ ASR 启动失败：{e}")
                print(f"     {backoff:.0f} 秒内不再重连\n")
                if gen == self.asr_gen:
                    self.pending_pcm = []
                    self.pending_pcm_started = False
                return

            # 成功了，清零失败计数
            self._asr_fail_count = 0
            self._asr_cooldown_until = 0.0

            # 建连期间这段已经翻篇 / 已退出对话态 → 丢掉这条连接
            if gen != self.asr_gen or self.state != "awake":
                asr.close()
                return

            # ⭐ 先补发缓存的音频，再挂到 self.asr 上。
            #    顺序反了的话，断句线程看到 self.asr 就以为可以收尾，
            #    但缓存里的头几百毫秒还没送进去 → 丢字。
            pending = self.pending_pcm
            self.pending_pcm = []
            for b in pending:
                try:
                    asr.feed(b)
                except Exception:
                    break

            self.asr = asr

        threading.Thread(target=run, daemon=True).start()

    def _leave_awake(self) -> None:
        self.state = "idle"
        self.speaking = False
        self.seg_buf = []
        self.pending_pcm = []
        self.pending_pcm_started = False
        self.asr_gen += 1
        asr = self.asr
        self.asr = None
        if asr is not None:
            threading.Thread(target=asr.close, daemon=True).start()

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
