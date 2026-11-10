#!/usr/bin/env python3
"""ChatLoop 分句逻辑测试（不联网、不用麦克风）。

防的是这个 bug：
    on_delta 里 `buf += d` 没写 nonlocal，
    Python 把 buf 当成局部变量 → UnboundLocalError，
    整个回复线程崩掉，用户看到的是「思考中」卡死。

用法：
    python test_chatloop.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class FakeSpeaker:
    """记录要说的话，不真合成。"""

    def __init__(self, api_key=None, voice="Cherry"):
        self.said: list[str] = []
        self.voice = voice

    def say(self, text):
        self.said.append(text)

    def stop(self):
        pass

    def reset(self):
        pass

    @property
    def speaking(self):
        return False

    @property
    def busy(self):
        return False


class FakeLLM:
    """按预置片段流式吐出，模拟 LLM。"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.received: list[str] = []

    def chat(self, text, on_delta=None, stream=True):
        self.received.append(text)
        whole = ""
        for c in self.chunks:
            whole += c
            if on_delta:
                on_delta(c)
            time.sleep(0.005)
        return whole


def make_loop(chunks):
    """构造一个 ChatLoop，但只替换聊天相关部分。"""
    with patch("phase3_chat.VoiceLoop") as MockVoice:
        MockVoice.return_value = _StubVoice()
        with patch("phase3_chat.Speaker", FakeSpeaker):
            with patch("phase3_chat.LLM", lambda *a, **kw: FakeLLM(chunks)):
                from phase3_chat import ChatLoop
                loop = ChatLoop(
                    asr_key="fake",
                    model_dir=HERE / "kws-model",
                )
    return loop


class _StubVoice:
    """VoiceLoop 的替身，只要属性够用就行。"""

    def __init__(self):
        self.state = "awake"
        self.speaking = False
        self.transcripts: list[str] = []

    def _render_status(self, rms):
        pass

    def run(self):
        pass


def wait_idle(loop, timeout=3.0):
    t0 = time.time()
    while getattr(loop, "thinking", False) and time.time() - t0 < timeout:
        time.sleep(0.01)


def case(name: str, chunks: list[str], expect_min: int, expect_max: int) -> bool:
    loop = make_loop(chunks)
    loop._on_user_said("测试输入")
    wait_idle(loop)

    said = loop.speaker.said
    ok = expect_min <= len(said) <= expect_max

    print(f"\n[{name}]")
    print(f"  LLM 片段：{len(chunks)} 个")
    print(f"  切出句子：{len(said)} 个（期望 {expect_min}~{expect_max}）")
    for i, s in enumerate(said, 1):
        print(f"    {i}. {s}")
    print(f"  {'✅' if ok else '❌'}")

    # 空片段也要能跑（不会崩）
    return ok


def case_empty() -> bool:
    """LLM 一个片段都不给 → 不能崩。"""
    loop = make_loop([])
    ok = True
    try:
        loop._on_user_said("测试输入")
        wait_idle(loop)
    except Exception as e:
        print(f"\n[空回复] ❌ 崩溃：{e}")
        return False

    ok = len(loop.speaker.said) == 0
    print(f"\n[空回复]  不崩溃、不念空 {'✅' if ok else '❌'}")
    return ok


def case_no_punctuation() -> bool:
    """长句没标点 → 攒到上限也要先念。"""
    # 120 个字，没有标点，MAX_BUFFER_CHARS = 40 → 应该切出 3 段
    chunks = ["这是一段很长的没有标点符号的文本" * 5]
    loop = make_loop(chunks)
    loop._on_user_said("测试")
    wait_idle(loop)
    said = loop.speaker.said
    ok = len(said) >= 2
    print(f"\n[无标点长句]")
    print(f"  原文 {len(chunks[0])} 字 → 切出 {len(said)} 段")
    for i, s in enumerate(said, 1):
        print(f"    {i}. {s}（{len(s)} 字）")
    print(f"  {'✅' if ok else '❌ 应该按 40 字硬切'}")
    return ok


def main() -> int:
    print("=== ChatLoop 分句测试 ===\n（假 LLM + 假 Speaker，不联网）")

    results = [
        case("标准两句", ["哼，本鱼是大肥鱼。", "才不是为你准备的呢！"], 2, 2),
        # 逗号不算句尾，"你好呀，今天想聊什么？" 只该切 1 句
        case("单句多片", ["你好", "呀，", "今天", "想聊什么？"], 1, 1),
        # 只有句号才切，逗号不切
        case("逗号不算句尾", ["哼，", "本鱼最近", "挺好的，", "就是有点饿。"], 1, 1),
        case_empty(),
        case_no_punctuation(),
    ]

    print("\n" + "=" * 40)
    passed = sum(1 for r in results if r)
    print(f"结果：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
