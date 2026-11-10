#!/usr/bin/env python3
"""录一段 WAV，用于离线测试唤醒词。

用法：
    python record_wav.py out.wav            # 录 3 秒
    python record_wav.py out.wav -d 5       # 录 5 秒
    python record_wav.py out.wav -d 3 -D 1  # 用 1 号设备
"""

from __future__ import annotations

import argparse
import time
import wave
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("out", type=Path, help="输出 WAV 路径")
    p.add_argument("-d", "--duration", type=float, default=3.0, help="录音秒数")
    p.add_argument("-D", "--device", type=int, default=None, help="输入设备编号")
    p.add_argument("-r", "--rate", type=int, default=16000, help="采样率（默认 16000）")
    args = p.parse_args()

    import numpy as np
    import sounddevice as sd

    # 挑设备：WASAPI/WDM-KS 优先（MME 常被其他程序独占）
    if args.device is not None:
        dev_index = args.device
        rate = args.rate
    else:
        host_rank = {"Windows WDM-KS": 0, "Windows WASAPI": 1,
                     "Windows DirectSound": 2, "MME": 3}
        cands = []
        for i, h in enumerate(sd.query_hostapis()):
            di = h.get("default_input_device", -1)
            if di is None or di < 0:
                continue
            dev = sd.query_devices(di)
            cands.append((host_rank.get(h["name"], 9), h["name"], di,
                          int(dev["default_samplerate"])))
        cands.sort()
        dev_index, host, rate = cands[0][2], cands[0][1], cands[0][3]
        print(f"主机 API：{host}")

    info = sd.query_devices(dev_index)
    print(f"设备：{info['name']}")
    print(f"采样率：{rate} Hz")

    print(f"录音 {args.duration} 秒，现在开始说 ...")
    for i in (3, 2, 1):
        print(f"  {i} ...", end="\r", flush=True)
        time.sleep(1)

    # 用 float32 录，再转 int16
    audio = sd.rec(
        int(args.duration * rate),
        samplerate=rate,
        channels=1,
        dtype="float32",
        device=dev_index,
    )
    sd.wait()

    print("录音完成： ", end="")
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio))))
    print(f"峰值 {peak:.3f} / RMS {rms:.3f}")
    if peak < 0.01:
        print("  ⚠️ 音量太低，可能没录到声音")

    # 降采样到 16k
    flat = audio.flatten()
    if rate != 16000:
        print(f"  重采样 {rate} → 16000 ...")
        n_out = int(round(len(flat) * 16000 / rate))
        x_old = np.linspace(0, 1, len(flat), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        flat = np.interp(x_new, x_old, flat)

    pcm = (np.clip(flat, -1.0, 1.0) * 32767).astype(np.int16)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())

    size = args.out.stat().st_size
    print(f"已保存：{args.out}  ({size/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
