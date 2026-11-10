#!/usr/bin/env python3
"""音色试听：把每个音色都念一遍，挑一个顺耳的。

用法：
    python preview_voices.py                    # 全部试听（约 30 秒）
    python preview_voices.py --voice Serena     # 只听一个
    python preview_voices.py --text "你好呀"     # 换试听文本
    python preview_voices.py --save             # 存成 wav 不播放

选好之后，把名字填到 DSH 设置页的「TTS 音色」里就行（改完立即生效）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# qwen3-tts-flash 实测可用的音色（按音高从高到低排）
VOICES = [
    ("Bella", "萝莉音", "最可爱，默认 ★"),
    ("Momo", "萝莉音", "软糯"),
    ("Mia", "少女音", "清亮"),
    ("Kiki", "少女音", "俏皮"),
    ("Cherry", "少女音", "甜美"),
    ("Serena", "少女音", "温柔"),
    ("Chelsie", "少女音", "活泼"),
    ("Sunny", "元气女声", "明亮"),
    ("Jada", "高音女声", "很尖"),
    ("Katerina", "御姐音", "低沉有气场"),
    ("Ethan", "男声", "低沉"),
    ("Peter", "男声", "沉稳"),
]

DEFAULT_TEXT = "哼，本鱼是大肥鱼，才不是特意为你准备的呢。"


def main() -> int:
    p = argparse.ArgumentParser(description="音色试听")
    p.add_argument("--voice", type=str, help="只听这个音色")
    p.add_argument("--text", type=str, default=DEFAULT_TEXT, help="试听文本")
    p.add_argument("--save", action="store_true", help="存成 wav 不播放")
    p.add_argument("--pause", type=float, default=0.8, help="每个之间停顿秒数")
    args = p.parse_args()

    from audio_player import Player
    from tts_qwen import TTSError, synthesize, wav_to_pcm
    from voice_config import load_keys

    keys = load_keys()
    if not keys.qwen:
        print("❌ 没有 qwen key")
        return 1

    targets = VOICES
    if args.voice:
        targets = [v for v in VOICES if v[0].lower() == args.voice.lower()] or [
            (args.voice, "自定义", "")
        ]

    out_dir = HERE / "voice-samples"
    if args.save:
        out_dir.mkdir(parents=True, exist_ok=True)

    player = Player() if not args.save else None

    print(f"试听文本：「{args.text}」\n")
    print(f"{'音色':<12}{'风格':<10}{'说明'}")
    print("-" * 50)

    ok = 0
    for name, style, note in targets:
        print(f"{name:<12}{style:<10}{note}", end="  ", flush=True)
        try:
            t0 = time.time()
            wav = synthesize(args.text, keys.qwen, name)
            pcm, rate = wav_to_pcm(wav)
            print(f"✓ {len(pcm)/rate:.1f}s / {time.time()-t0:.1f}s", flush=True)

            if args.save:
                path = out_dir / f"{name}.wav"
                path.write_bytes(wav)
            else:
                player.play_pcm(pcm, rate)
                time.sleep(args.pause)
            ok += 1
        except TTSError as e:
            print(f"✗ {str(e)[:50]}")

    print("-" * 50)
    print(f"完成 {ok}/{len(targets)}")

    if args.save:
        print(f"\n已存到：{out_dir}")
    else:
        print("\n选好之后，去 DSH 设置页把「TTS 音色」改成对应名字（改完下一句生效）。")

    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
