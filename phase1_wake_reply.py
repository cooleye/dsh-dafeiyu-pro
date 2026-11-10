#!/usr/bin/env python3
"""Phase 1 Demo：唤醒 → 说「我在」

完整验证这条链路：

    麦克风 → KWS 唤醒检测 → 命中 → 播放回应 → 回到监听

这已经是一个可用的「语音唤醒 + 回应」功能了，虽然还不能对话。

用法：
    python phase1_wake_reply.py                    # 默认回应「我在」
    python phase1_wake_reply.py --reply "在呢，有什么事"
    python phase1_wake_reply.py --cloud-tts        # 用云端 TTS（需配置，Phase 2）
    python phase1_wake_reply.py --device 59

Ctrl+C 退出。
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audio_player import Player, sapi_speak  # noqa: E402
from phase0_kws_poc import create_spotter, pick_input_device, resample_to_16k  # noqa: E402


class WakeDetector:
    """唤醒检测 + 回应 的完整闭环。"""

    def __init__(
        self,
        model_dir: Path,
        reply_text: str = "我在",
        device: int | None = None,
        score: float = 3.0,
        threshold: float = 0.1,
        cooldown: float = 2.0,
    ):
        self.spotter, self.stream = create_spotter(
            model_dir, score=score, threshold=threshold
        )
        self.reply_text = reply_text
        self.player = Player()
        self.cooldown = cooldown
        self.last_wake = 0.0
        self.wake_count = 0

        self.device, detected_rate, desc = pick_input_device(device)
        self.sample_rate = detected_rate
        self.native_16k = self.sample_rate == 16000
        self.device_desc = desc

    def run(self) -> None:
        print("=== 唤醒 → 回应 Demo ===\n")
        print(f"  设备：{self.device_desc}")
        print(f"  采样率：{self.sample_rate} Hz"
              + ("" if self.native_16k else " → 重采样 16000"))
        print(f"  回应语：「{self.reply_text}」")
        print(f"  冷却：{self.cooldown} 秒（防止连续触发）\n")
        print("说「大肥鱼」，它会回你。Ctrl+C 退出。\n")

        block_size = int(self.sample_rate * 0.1)
        audio_q: queue.Queue = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status:
                print(f"  [音频] {status}", file=sys.stderr)
            audio_q.put(indata.copy())

        t_start = time.time()

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=block_size,
                device=self.device,
                callback=callback,
            ):
                while True:
                    block = audio_q.get()
                    samples = block.flatten()

                    if not self.native_16k:
                        samples = np.array(
                            resample_to_16k(samples.tolist(), self.sample_rate),
                            dtype=np.float32,
                        )

                    # 音量条
                    rms = float(np.sqrt(np.mean(np.square(samples))))
                    bar = "█" * int(min(rms * 300, 30))
                    print(f"\r  音量 {rms:.3f} {bar:<30}", end="", flush=True)

                    # 喂给 KWS
                    self.stream.accept_waveform(16000, samples.tolist())
                    hit = None
                    while self.spotter.is_ready(self.stream):
                        self.spotter.decode_stream(self.stream)
                        result = self.spotter.get_result(self.stream)
                        if result:
                            hit = result.strip()
                            self.spotter.reset_stream(self.stream)
                            break

                    if not hit:
                        continue

                    # 冷却检查
                    now = time.time()
                    if now - self.last_wake < self.cooldown:
                        continue
                    self.last_wake = now
                    self.wake_count += 1
                    elapsed = now - t_start

                    print(f"\n\n  🎯 [{elapsed:7.1f}s] 第 {self.wake_count} 次唤醒：{hit}")

                    # 回应
                    print(f"  🔊 回应：「{self.reply_text}」")
                    sapi_speak(self.reply_text)
                    print("  ✓ 回应完毕，继续监听\n")

        except KeyboardInterrupt:
            print(f"\n\n=== 统计 ===")
            print(f"运行 {time.time() - t_start:.1f} 秒，唤醒 {self.wake_count} 次")


def main() -> int:
    p = argparse.ArgumentParser(description="唤醒 → 回应 Demo")
    p.add_argument("--model-dir", type=Path, default=HERE / "kws-model")
    p.add_argument("--reply", type=str, default="我在", help="唤醒后的回应语")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--score", type=float, default=3.0)
    p.add_argument("--threshold", type=float, default=0.1)
    p.add_argument("--cooldown", type=float, default=2.0, help="唤醒冷却秒数")
    args = p.parse_args()

    detector = WakeDetector(
        model_dir=args.model_dir,
        reply_text=args.reply,
        device=args.device,
        score=args.score,
        threshold=args.threshold,
        cooldown=args.cooldown,
    )
    detector.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
