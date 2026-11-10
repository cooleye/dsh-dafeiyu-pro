#!/usr/bin/env python3
"""Phase 3 集成测试：文字 → LLM → 分句 → TTS → 播放

不用麦克风、不用唤醒词，直接验证对话管线。
（麦克风那段已在 Phase 2 验证过了）

用法：
    python test_phase3.py                        # 默认一句
    python test_phase3.py --say "你是谁"          # 自定义
    python test_phase3.py --no-audio             # 只跑 LLM，不合成不播放
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--say", type=str, default="用两句话介绍你自己")
    p.add_argument("--voice", type=str, default="Cherry")
    p.add_argument("--llm", type=str, default="qwen-flash")
    p.add_argument("--no-audio", action="store_true", help="只跑 LLM，跳过 TTS/播放")
    args = p.parse_args()

    from llm_qwen import LLM, LLMError
    from voice_config import load_keys

    keys = load_keys()
    if not keys.qwen:
        print("❌ 没有 qwen key")
        return 1

    llm = LLM(keys.qwen, model=args.llm)

    print(f"=== 用户：{args.say} ===\n")
    print("🐟 ", end="", flush=True)

    t0 = time.time()
    first = [None]
    buf = ""
    sentences: list[str] = []

    import re
    SENT_END = re.compile(r"[。！？!?；;\n]+")

    def on_delta(d: str):
        if first[0] is None:
            first[0] = time.time()
        print(d, end="", flush=True)
        nonlocal buf
        buf += d
        while True:
            m = SENT_END.search(buf)
            if not m:
                break
            s = buf[:m.start()].strip()
            if len(s) >= 2:
                sentences.append(s)
            buf = buf[m.end():]

    try:
        llm.chat(args.say, on_delta=on_delta)
    except LLMError as e:
        print(f"\n❌ LLM 失败：{e}")
        return 1

    if buf.strip():
        sentences.append(buf.strip())

    ttft = (first[0] - t0) if first[0] else 0
    total = time.time() - t0

    print(f"\n\n首字 {ttft:.2f}s / 总长 {total:.2f}s")
    print(f"切出 {len(sentences)} 句：")
    for i, s in enumerate(sentences, 1):
        print(f"  {i}. {s}")

    if args.no_audio:
        print("\n（--no-audio，跳过合成）")
        return 0

    # ---- TTS + 播放 ----
    from audio_player import Player
    from tts_qwen import TTSError, synthesize, wav_to_pcm

    player = Player()
    print(f"\n=== 合成并播放（音色 {args.voice}）===")

    ok = 0
    for i, s in enumerate(sentences, 1):
        t1 = time.time()
        try:
            wav = synthesize(s, keys.qwen, args.voice)
            pcm, rate = wav_to_pcm(wav)
        except TTSError as e:
            print(f"  {i}. ❌ {e}")
            continue
        tts_ms = (time.time() - t1) * 1000
        dur = len(pcm) / rate
        print(f"  {i}. {s[:24]}…  {dur:.2f}s 音频 / 合成 {tts_ms:.0f}ms")
        player.play_pcm(pcm, rate)
        ok += 1

    print(f"\n=== 完成：{ok}/{len(sentences)} 句成功 ===")
    return 0 if ok == len(sentences) else 1


if __name__ == "__main__":
    raise SystemExit(main())
