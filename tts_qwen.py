#!/usr/bin/env python3
"""阿里云 Qwen-TTS 语音合成。

HTTP 版（非流式）：一次请求拿一整段 WAV。
比 WebSocket 流式简单得多，首字延迟 1~2 秒，对短句够用。

协议：
    POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
    { "model": "qwen3-tts-flash",
      "input": { "text": "...", "voice": "Cherry", "instruct": "用开心的语气" } }
    → { "output": { "audio": { "url": "https://...wav" } } }
    → 下载 URL 得到 WAV

用法：
    python tts_qwen.py --say "你好，我是大肥鱼"
    python tts_qwen.py --say "..." --voice Serena --out out.wav
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

API_URL = ("https://dashscope.aliyuncs.com/api/v1/services/"
           "aigc/multimodal-generation/generation")
MODEL = "qwen3-tts-flash"

# 常用音色（Cherry / Serena / Chelsie / Ethan / Chelsie 等）
DEFAULT_VOICE = "Cherry"


class TTSError(RuntimeError):
    pass


def synthesize(
    text: str,
    api_key: str,
    voice: str = DEFAULT_VOICE,
    instruct: str = "",
    timeout: float = 45.0,
) -> bytes:
    """合成语音，返回 WAV 字节。"""

    text = (text or "").strip()
    if not text:
        raise TTSError("合成文本为空")

    payload = {
        "model": MODEL,
        "input": {"text": text, "voice": voice},
    }
    if instruct.strip():
        payload["input"]["instruct"] = instruct.strip()

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise TTSError(f"HTTP {e.code}：{detail}") from e
    except Exception as e:
        raise TTSError(f"请求失败：{e}") from e

    try:
        data = json.loads(raw)
    except Exception:
        raise TTSError(f"响应不是 JSON：{raw[:200]!r}")

    audio = (data.get("output") or {}).get("audio") or {}
    inline = audio.get("data") or ""
    url = audio.get("url") or ""

    if inline:
        import base64
        wav = base64.b64decode(inline)
    elif url:
        wav = _download(url, timeout)
    else:
        raise TTSError(f"响应里没有音频：{json.dumps(data, ensure_ascii=False)[:250]}")

    if len(wav) < 12 or wav[:4] != b"RIFF":
        raise TTSError("返回的不是 WAV")

    return wav


def _download(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-dafeiyu-pro"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def wav_to_pcm(wav: bytes) -> tuple:
    """把 WAV 拆成 (float32 采样, 采样率)。"""
    import array

    import numpy as np

    with wave.open(io.BytesIO(wav), "rb") as w:
        ch = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise TTSError(f"只支持 16-bit，实际 {width * 8}-bit")

    a = array.array("h")
    a.frombytes(frames)
    if ch > 1:
        a = a[::ch]

    return np.array(a, dtype=np.float32) / 32768.0, rate


def main() -> int:
    p = argparse.ArgumentParser(description="Qwen-TTS 语音合成")
    p.add_argument("--say", type=str, required=True, help="要合成的文本")
    p.add_argument("--voice", type=str, default=DEFAULT_VOICE)
    p.add_argument("--instruct", type=str, default="", help="情感指令，如「用开心的语气」")
    p.add_argument("--out", type=Path, help="保存 WAV（不给就直接播放）")
    p.add_argument("--key", type=str)
    args = p.parse_args()

    key = args.key
    if not key:
        from voice_config import load_keys
        key = load_keys().qwen
    if not key:
        print("❌ 没有 qwen key")
        return 1

    import time
    t0 = time.time()
    try:
        wav = synthesize(args.say, key, args.voice, args.instruct)
    except TTSError as e:
        print(f"❌ {e}")
        return 1
    elapsed = time.time() - t0

    pcm, rate = wav_to_pcm(wav)
    duration = len(pcm) / rate

    print(f"✅ 合成成功")
    print(f"   文本：{args.say}")
    print(f"   音色：{args.voice}")
    print(f"   音频：{len(wav)/1024:.1f} KB / {duration:.2f} 秒 @ {rate}Hz")
    print(f"   耗时：{elapsed:.2f} 秒"
          + (f"（首字节 {elapsed:.2f}s，{duration/elapsed:.1f}× 实时）" if elapsed else ""))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(wav)
        print(f"   已保存：{args.out}")
    else:
        print("   ▶ 播放中 ...")
        from audio_player import Player
        Player().play_pcm(pcm, rate)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
