#!/usr/bin/env python3
"""Phase 3 Demo：完整语音对话

    ┌──────────────────────────────────────────┐
    │  说「大肥鱼」                             │
    │      ↓                                    │
    │  本地 KWS 命中 → 说「我在」               │
    │      ↓                                    │
    │  进入对话态，VAD 断句                     │
    │      ↓                                    │
    │  云端流式 ASR                             │
    │      ↓                                    │
    │  LLM 流式生成（人设 + 多轮历史）           │
    │      ↓  按句切分                          │
    │  云端 TTS → 播报                          │
    └──────────────────────────────────────────┘

带打断：说话时你开口，它立刻闭嘴。

用法：
    python phase3_chat.py
    python phase3_chat.py --voice Serena
    python phase3_chat.py --no-bargein     # 关掉打断
"""

from __future__ import annotations

import argparse
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audio_player import Player  # noqa: E402
from llm_qwen import LLM, LLMError, Persona  # noqa: E402
from phase2_listen import VoiceLoop  # noqa: E402
from tts_qwen import TTSError, synthesize, wav_to_pcm  # noqa: E402
from voice_config import load_keys  # noqa: E402

# 句子边界：中文句号/问号/叹号/分号，以及换行
SENTENCE_END = re.compile(r"[。！？!?；;\n]+")
# 太短的片段不单独合成（避免一个字一个字念）
MIN_SENTENCE_CHARS = 2
# 攒够这么多字即使没有标点也先念（防止长句憋着不出声）
MAX_BUFFER_CHARS = 40
# 长句硬切时优先在这些地方断，念起来更自然
SOFT_BREAK = "，、,：: "


def split_long(text: str, limit: int = MAX_BUFFER_CHARS) -> tuple[str, str]:
    """把过长的文本切出一段（不超过 limit），返回 (head, tail)。

    优先在逗号/顿号/空格处断；找不到就硬切。
    硬切时会在 limit 附近往回找 SOFT_BREAK，最多回退 1/3。
    """
    if len(text) <= limit:
        return text, ""

    # 在 [limit*2//3, limit] 区间里找最靠后的软断点
    lo = max(1, limit * 2 // 3)
    for i in range(limit, lo - 1, -1):
        if i < len(text) and text[i - 1] in SOFT_BREAK:
            return text[:i], text[i:]
    # 没有软断点 → 硬切
    return text[:limit], text[limit:]


class Speaker:
    """把文字变成声音并播放。

    生产者-消费者：
      - LLM 流式产出文字 → 切成句子 → 入队
      - 消费端逐句合成 + 播放
    这样第一句合成完就能开口，不用等整段回复。
    """

    def __init__(self, api_key: str, voice: str = "Cherry"):
        self.api_key = api_key
        self.voice = voice
        self.player = Player()
        self.q: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.busy = False            # 正在说话（给打断检测用）
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def say(self, text: str) -> None:
        """排入一句要念的话。"""
        text = (text or "").strip()
        if text:
            self.q.put(text)

    def stop(self) -> None:
        """立刻闭嘴（清空队列 + 停播放）。"""
        self.stop_flag.set()
        self.player.stop()
        # 清空待念队列
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def reset(self) -> None:
        self.stop_flag.clear()

    @property
    def speaking(self) -> bool:
        return self.busy or not self.q.empty()

    def _run(self) -> None:
        while True:
            text = self.q.get()
            if text is None:
                return
            if self.stop_flag.is_set():
                continue
            self.busy = True
            try:
                wav = synthesize(text, self.api_key, self.voice)
                pcm, rate = wav_to_pcm(wav)
                self.player.play_pcm(pcm, rate)
            except TTSError as e:
                print(f"\n  ❌ TTS 失败：{e}\n")
            except Exception as e:
                print(f"\n  ❌ 播放失败：{e}\n")
            finally:
                self.busy = False


class ChatLoop:
    """语音对话主循环。"""

    def __init__(
        self,
        asr_key: str,
        model_dir: Path,
        voice: str = "Cherry",
        llm_model: str = "qwen-flash",
        device: int | None = None,
        bargein: bool = True,
        dialogue_timeout: float = 30.0,
        system_prompt: str = "",
    ):
        self.bargein = bargein
        self.llm = LLM(asr_key, model=llm_model, system=system_prompt)
        self.speaker = Speaker(asr_key, voice)
        self.thinking = False

        self.loop = VoiceLoop(
            api_key=asr_key,
            model_dir=model_dir,
            wake_reply="我在",
            device=device,
            dialogue_timeout=dialogue_timeout,
            on_transcript=self._on_user_said,
        )

        # 打断：每帧回调，检测用户开口
        self._orig_render = self.loop._render_status
        self.loop._render_status = self._render

    # ---------- 主流程 ----------

    def run(self) -> None:
        print("=== 语音对话 Demo ===\n")
        print(f"  模型：{self.llm.model}")
        print(f"  音色：{self.speaker.voice}")
        print(f"  打断：{'开' if self.bargein else '关'}")
        print(f"  人设：{Persona.build()[:28].replace(chr(10), ' ')}...")
        print("\n说「大肥鱼」叫醒它，然后聊天。Ctrl+C 退出。\n")
        self.loop.run()

    def _on_user_said(self, text: str) -> None:
        """ASR 出一句 → 送给 LLM。"""
        # 正在说话时用户开口 → 先闭嘴（打断）
        if self.bargein and self.speaker.speaking:
            print("  🤐 打断，停止播报")
            self.speaker.stop()

        self.speaker.reset()
        self.thinking = True
        print(f"  🤔 思考中 ...")

        def work():
            t0 = time.time()
            first_delta = [None]
            buf = ""
            spoken = []

            def flush(sent: str):
                s = sent.strip()
                if len(s) >= MIN_SENTENCE_CHARS:
                    self.speaker.say(s)
                    spoken.append(s)

            def on_delta(d):
                nonlocal buf
                if first_delta[0] is None:
                    first_delta[0] = time.time()
                print(d, end="", flush=True)
                buf += d
                # 攒到句子边界就切
                while True:
                    m = SENTENCE_END.search(buf)
                    if not m:
                        break
                    flush(buf[:m.start()])
                    buf = buf[m.end():]
                # 太长还没标点，也先切一段念（防止长句憋着不出声）
                while len(buf) >= MAX_BUFFER_CHARS:
                    head, buf = split_long(buf)
                    flush(head)

            try:
                print("\n  🐟 ", end="", flush=True)
                reply = self.llm.chat(text, on_delta=on_delta)
                flush(buf)   # 最后残留的
                ttft = (first_delta[0] - t0) if first_delta[0] else 0
                total = time.time() - t0
                print(f"\n  [{ttft:.2f}s 首字 / {total:.2f}s 生成 / {len(spoken)} 句]\n")
                if not reply.strip():
                    print("  （空回复）\n")
            except LLMError as e:
                print(f"\n  ❌ 回复失败：{e}\n")
                self.speaker.say("呜……我这边卡住了")
            finally:
                self.thinking = False

        threading.Thread(target=work, daemon=True).start()

    # ---------- 界面 ----------

    def _render(self, rms: float) -> None:
        if self.thinking:
            icon = "🤔"
        elif self.speaker.busy:
            icon = "🗣"
        elif self.loop.speaking:
            icon = "🔴"
        elif self.loop.state == "awake":
            icon = "👂"
        else:
            icon = "💤"
        bar = "█" * int(min(rms * 200, 24))
        print(f"\r {icon} {rms:.3f} {bar:<24}", end="", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="完整语音对话 Demo")
    p.add_argument("--model-dir", type=Path, default=HERE / "kws-model")
    p.add_argument("--voice", type=str, default="Cherry",
                   help="TTS 音色（Cherry / Serena / Chelsie / Ethan）")
    p.add_argument("--llm", type=str, default="qwen-flash")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--timeout", type=float, default=30.0,
                   help="唤醒后无语音的退回时间（秒）")
    p.add_argument("--no-bargein", action="store_true", help="关掉打断")
    p.add_argument("--persona", type=str, default="", help="自定义人设（覆盖默认）")
    p.add_argument("--persona-file", type=Path, help="从文件读人设")
    args = p.parse_args()

    keys = load_keys()
    if not keys.qwen:
        print("❌ 没找到 qwen API Key")
        return 1

    system = args.persona
    if args.persona_file and args.persona_file.exists():
        system = args.persona_file.read_text(encoding="utf-8")

    loop = ChatLoop(
        asr_key=keys.qwen,
        model_dir=args.model_dir,
        voice=args.voice,
        llm_model=args.llm,
        device=args.device,
        bargein=not args.no_bargein,
        dialogue_timeout=args.timeout,
        system_prompt=system,
    )
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
