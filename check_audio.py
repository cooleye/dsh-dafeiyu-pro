#!/usr/bin/env python3
"""音频自检：查清楚「明明在回复，却听不到声音」是哪个环节断了。

会依次做四件事：
  1. 列出所有输出/输入设备，标出当前默认
  2. 在默认输出上放一段明显的语音（不是哔声，是 TTS）
  3. 逐个候选设备放哔声，让你听出到底哪个能响
  4. 用麦克风录 2 秒，验证输入端有没有信号

用法：
    python check_audio.py            # 全套
    python check_audio.py --beeps    # 只在各设备上空响一遍
    python check_audio.py --mic      # 只测麦克风
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def tone(freq: float = 660.0, dur: float = 1.0, rate: int = 44100) -> np.ndarray:
    t = np.linspace(0, dur, int(rate * dur), endpoint=False)
    # 加淡入淡出，避免爆音
    wave = 0.35 * np.sin(2 * np.pi * freq * t)
    fade = min(2000, len(wave) // 10)
    if fade:
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
    return wave.astype(np.float32)


def show_devices() -> None:
    default_in, default_out = sd.default.device
    print("=== 输出设备 ===")
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            tag = "  ← 当前默认" if i == default_out else ""
            print(f"  [{i:3d}] {d['name'][:44]:<44}{tag}")
    print("\n=== 输入设备 ===")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            tag = "  ← 当前默认" if i == default_in else ""
            print(f"  [{i:3d}] {d['name'][:44]:<44}{tag}")
    print()


def play_tts() -> None:
    """用真实 TTS 在默认输出上放一句话。"""
    from audio_player import Player
    from tts_qwen import synthesize, wav_to_pcm
    from voice_config import load_keys

    keys = load_keys()
    if not keys.qwen:
        print("⚠️ 没有 qwen key，跳过 TTS 测试")
        return
    text = "本鱼现在正在用默认输出设备说话。如果你听得到，说明播放没问题。"
    print(f"🔊 在默认输出上放：{text}")
    try:
        wav = synthesize(text, keys.qwen, "Bella")
        pcm, rate = wav_to_pcm(wav)
        Player().play_pcm(pcm, rate)
        print("   ✅ 播放调用完成（有没有声音要你自己听）")
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")


def play_beeps() -> None:
    """每个候选输出设备放一声，让人听出哪个能响。"""
    default_out = sd.default.device[1]
    seen: set[str] = set()
    candidates: list[tuple[int, str]] = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] <= 0:
            continue
        name = d["name"]
        if name in seen:
            continue
        seen.add(name)
        candidates.append((i, name))

    print("=== 逐个设备放哔声（每声之间有 0.6 秒间隔）===")
    print("请留意：**哪一串哔声是你能听到的**，记下它的编号。\n")
    for i, name in candidates:
        try:
            sd.play(tone(), 44100, device=i)
            sd.wait()
            print(f"  [{i:3d}] {name[:44]:<44} 已播放")
        except Exception as e:
            print(f"  [{i:3d}] {name[:44]:<44} ❌ {str(e)[:40]}")
        time.sleep(0.6)
    print(f"\n当前默认输出 = [{default_out}]")
    print("如果某一串你听到了、但它不是默认设备，把它的编号告诉大肥鱼。")


def test_mic(seconds: float = 3.0) -> None:
    """录一段，验证输入端有没有信号。"""
    dev = sd.default.device[0]
    info = sd.query_devices(dev)
    rate = int(info["default_samplerate"])
    print(f"🎤 用默认输入 [{dev}] {info['name'][:40]} 录 {seconds:.0f} 秒，说句话…")
    rec = sd.rec(int(seconds * rate), samplerate=rate, channels=1,
                 dtype="float32", device=dev)
    sd.wait()
    flat = rec.flatten()
    peak = float(np.max(np.abs(flat)))
    rms = float(np.sqrt(np.mean(flat ** 2)))
    print(f"   峰值={peak:.4f}  RMS={rms:.4f}")
    if peak > 0.05:
        print("   ✅ 麦克风正常")
    elif peak > 0.005:
        print("   ⚠️ 有信号但很小，麦克风音量可能太低")
    else:
        print("   ❌ 几乎没信号：设备选错了，或被静音")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--beeps", action="store_true", help="只在各设备上响一遍")
    p.add_argument("--mic", action="store_true", help="只测麦克风")
    p.add_argument("--devices", action="store_true", help="只列设备")
    args = p.parse_args()

    if args.devices:
        show_devices()
        return 0
    if args.beeps:
        play_beeps()
        return 0
    if args.mic:
        test_mic()
        return 0

    show_devices()
    play_tts()
    print()
    play_beeps()
    print()
    test_mic()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
