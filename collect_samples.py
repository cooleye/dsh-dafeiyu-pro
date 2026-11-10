#!/usr/bin/env python3
"""采集唤醒词测试样本。

用途：建立正/负样本集，用来评估真阳性率和假阳性率。

用法：
    # 采集正样本（说「大肥鱼」）
    python collect_samples.py pos --count 10

    # 采集负样本（说日常的话，不该命中的）
    python collect_samples.py neg --count 10 --script "今天天气不错,我在写代码,这个功能挺难的"

    # 指定设备
    python collect_samples.py pos --count 5 --device 59
"""

from __future__ import annotations

import argparse
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent
SAMPLES_ROOT = HERE / "samples"


def pick_device() -> tuple[int, int, str]:
    host_rank = {"Windows WDM-KS": 0, "Windows WASAPI": 1,
                 "Windows DirectSound": 2, "MME": 3}
    cands = []
    for h in sd.query_hostapis():
        di = h.get("default_input_device", -1)
        if di is None or di < 0:
            continue
        dev = sd.query_devices(di)
        cands.append((host_rank.get(h["name"], 9), h["name"], di,
                      int(dev["default_samplerate"]), dev["name"]))
    cands.sort()
    _, host, di, rate, name = cands[0]
    return di, rate, f"{host} / {name}"


def record_one(seconds: float, device: int, rate: int) -> np.ndarray:
    print("  3 ...", end="", flush=True); time.sleep(1)
    print("\r  2 ...", end="", flush=True); time.sleep(1)
    print("\r  1 ...", end="", flush=True); time.sleep(1)
    print("\r  🎤 说 ...", end="", flush=True)

    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1,
                   dtype="float32", device=device)
    sd.wait()
    print("\r" + " " * 20 + "\r", end="")
    return audio.flatten()


def save_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """保存为 16k 单声道 16-bit WAV。"""
    if rate != 16000:
        n_out = int(round(len(samples) * 16000 / rate))
        x_old = np.linspace(0, 1, len(samples), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        samples = np.interp(x_new, x_old, samples)

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["pos", "neg"],
                   help="pos=正样本（说唤醒词），neg=负样本（日常说话）")
    p.add_argument("--count", type=int, default=10, help="采集条数")
    p.add_argument("--seconds", type=float, default=2.5, help="每条时长")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--script", type=str, default=None,
                   help="负样本要说的话，逗号分隔（轮换提示）")
    args = p.parse_args()

    dev, rate, desc = (args.device, int(sd.query_devices(args.device)["default_samplerate"]), "手动指定") \
        if args.device is not None else pick_device()

    print(f"设备：{desc}")
    print(f"采样率：{rate} Hz\n")

    out_dir = SAMPLES_ROOT / args.kind
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = len(list(out_dir.glob("*.wav")))
    print(f"目标目录：{out_dir}")
    print(f"已有 {existing} 个样本\n")

    prompts = []
    if args.kind == "neg" and args.script:
        prompts = [s.strip() for s in args.script.split(",") if s.strip()]

    if args.kind == "pos":
        print("=== 采集正样本 ===")
        print("每次提示后，清楚地说「大肥鱼」。\n")
    else:
        print("=== 采集负样本 ===")
        print("每次说一句日常的话（不要说唤醒词）。\n")

    saved = 0
    try:
        for i in range(args.count):
            idx = existing + i + 1
            if prompts:
                line = prompts[(i) % len(prompts)]
                print(f"[{i+1}/{args.count}] 请说：「{line}」")
            elif args.kind == "pos":
                print(f"[{i+1}/{args.count}] 请说：「大肥鱼」")
            else:
                print(f"[{i+1}/{args.count}] 请随便说一句日常的话")

            audio = record_one(args.seconds, dev, rate)
            peak = float(np.max(np.abs(audio)))
            rms = float(np.sqrt(np.mean(audio ** 2)))

            if peak < 0.01:
                print(f"  ⚠️ 音量太低（峰值 {peak:.3f}），跳过这条")
                continue

            path = out_dir / f"{args.kind}_{idx:03d}.wav"
            save_wav(path, audio, rate)
            print(f"  ✓ 已保存 {path.name}  (峰值 {peak:.3f}, RMS {rms:.3f})")
            saved += 1
            time.sleep(0.3)

    except KeyboardInterrupt:
        print("\n\n已中断")

    print(f"\n=== 完成：新增 {saved} 个样本，目录共 {len(list(out_dir.glob('*.wav')))} 个 ===")
    print(f"\n下一步：")
    if args.kind == "pos":
        print(f"  再采集负样本：python collect_samples.py neg --count 10")
    print(f"  评估：python phase0_kws_poc.py --eval ./samples/pos --neg ./samples/neg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
