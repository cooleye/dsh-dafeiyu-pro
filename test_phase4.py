#!/usr/bin/env python3
"""Phase 4 测试：分流分发 + 垫场 + 结果清洗

不联网、不用麦克风（除了最后的真实任务测试，需加 --live）。

用法：
    python test_phase4.py           # 只测逻辑
    python test_phase4.py --live    # 额外跑一次真实 DSH agent 任务
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class FakeSpeaker:
    def __init__(self, *a, **kw):
        self.said: list[str] = []

    def say(self, t):
        self.said.append(t)

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
    def __init__(self, *a, **kw):
        self.history = []

    def chat(self, text, on_delta=None, stream=True):
        msg = f"闲聊回复：{text}"
        if on_delta:
            for part in [msg[:5], msg[5:]]:
                on_delta(part)
        return msg

    def chat_with_tools(self, text, tools, on_delta=None, on_tool=None, **kw):
        if on_tool:
            on_tool("web_search", '{"query":"x"}')
        msg = f"查数据回复：{text}"
        if on_delta:
            on_delta(msg)
        return msg, ["web_search"]


class FakeAgent:
    def __init__(self, *a, **kw):
        self.calls: list[str] = []
        self.delay = 0.05
        self.result = ("ok", "任务结果：这是干完活的产出。")

    def run_async(self, task, on_done):
        self.calls.append(task)

        def w():
            time.sleep(self.delay)
            on_done(*self.result)

        import threading
        threading.Thread(target=w, daemon=True).start()

    def cancel(self):
        pass


class StubVoice:
    def __init__(self, *a, **kw):
        self.state = "awake"
        self.speaking = False

    def _render_status(self, rms):
        pass

    def run(self):
        pass


def make_app(use_agent=True, agent_delay=0.05):
    with patch("phase4_assistant.VoiceLoop", lambda *a, **kw: StubVoice()), \
         patch("phase4_assistant.Speaker", FakeSpeaker), \
         patch("phase4_assistant.LLM", FakeLLM), \
         patch("phase4_assistant.Tools", lambda *a, **kw: None), \
         patch("phase4_assistant.DshAgent", lambda *a, **kw: FakeAgent()):
        from phase4_assistant import Assistant
        app = Assistant(
            api_key="fake",
            search_key="",
            deepseek_key="",
            use_agent=use_agent,
            stall_delay=0.1,
        )
    if app.agent:
        app.agent.delay = agent_delay
    return app


def wait_idle(app, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not app.thinking and not app.agent_busy and not app.speaker.busy:
            time.sleep(0.05)
            return True
        time.sleep(0.02)
    return False


# ---------- 测试用例 ----------


def t_route_chat() -> bool:
    app = make_app()
    app._on_user_said("你好呀")
    wait_idle(app)
    ok = app.current_route.kind == "chat" and any("闲聊回复" in s for s in app.speaker.said)
    print(f"\n[闲聊走直连]  分流={app.current_route.kind}  {'✅' if ok else '❌'}")
    print(f"  说了：{app.speaker.said}")
    return ok


def t_route_data() -> bool:
    app = make_app()
    app._on_user_said("今天天气怎么样")
    wait_idle(app)
    ok = (app.current_route.kind == "data"
          and any("查数据回复" in s for s in app.speaker.said))
    print(f"\n[查数据走工具]  分流={app.current_route.kind}  {'✅' if ok else '❌'}")
    print(f"  说了：{app.speaker.said}")
    return ok


def t_route_task() -> bool:
    app = make_app()
    app._on_user_said("帮我写一个 Python 脚本")
    ok = app.current_route.kind == "task" and app.agent_busy
    wait_idle(app)
    said = app.speaker.said
    ok = ok and len(said) >= 2 and said[0].startswith("好的")
    print(f"\n[任务走 agent]  分流={app.current_route.kind}  {'✅' if ok else '❌'}")
    print(f"  派活了：{app.agent.calls}")
    print(f"  说了：{said}")
    return ok


def t_stall_second() -> bool:
    """任务超过垫场延迟 → 应该补第二句。"""
    app = make_app(agent_delay=0.45)
    app.stall_delay = 0.1
    app._on_user_said("帮我写一个文档")
    wait_idle(app, timeout=5)
    said = app.speaker.said
    ok = len(said) >= 3 and "花点时间" in "".join(said)
    print(f"\n[两级垫场]  {'✅' if ok else '❌'}")
    print(f"  说了：{said}")
    return ok


def t_no_agent() -> bool:
    """关掉 agent 时，任务类也要能走 LLM 兜底。"""
    app = make_app(use_agent=False)
    app._on_user_said("帮我写一个 Python 脚本")
    wait_idle(app)
    ok = any("查数据回复" in s or "闲聊回复" in s for s in app.speaker.said)
    print(f"\n[关掉 agent 兜底]  {'✅' if ok else '❌'}")
    print(f"  说了：{app.speaker.said}")
    return ok


def t_clean_speech() -> bool:
    """Markdown 清洗。"""
    from phase4_assistant import clean_for_speech

    src = """## 标题
这是**重点**内容，包含 `code` 和 [链接](http://x)。

```python
print("hello")
```

- 列表项1
- 列表项2
"""
    out = clean_for_speech(src)
    bad = ["#", "**", "```", "print(", "](", "- 列表"]
    ok = not any(b in out for b in bad) and "重点" in out and "列表项1" in out
    print(f"\n[Markdown 清洗]  {'✅' if ok else '❌'}")
    print(f"  原始 {len(src)} 字 → 清洗后 {len(out)} 字")
    print(f"  结果：{out!r}")
    return ok


def t_truncate() -> bool:
    """超长结果要截断。"""
    from phase4_assistant import MAX_SPEAK_CHARS, clean_for_speech

    long_text = "这是一句话。" * 100
    out = clean_for_speech(long_text)
    ok = len(out) <= MAX_SPEAK_CHARS + 10
    print(f"\n[超长截断]  {len(long_text)} 字 → {len(out)} 字  {'✅' if ok else '❌'}")
    return ok


def t_live_agent() -> bool:
    """真实跑一次 DSH agent。"""
    print("\n[真实 DSH agent] 启动中（可能要十几秒）...")
    from dsh_agent import DshAgent
    from voice_config import load_keys

    keys = load_keys()
    agent = DshAgent(api_key=keys.deepseek, timeout=180)
    t0 = time.time()
    ok, out = agent.run("只回复四个字：任务完成")
    elapsed = time.time() - t0
    print(f"  {'✅' if ok else '❌'} {elapsed:.1f}s → {out[:100]!r}")
    return ok and bool(out.strip())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="额外跑真实 DSH agent")
    args = p.parse_args()

    print("=== Phase 4 测试 ===\n（假 LLM / 假 Speaker / 假 Agent）")

    results = [
        t_route_chat(),
        t_route_data(),
        t_route_task(),
        t_stall_second(),
        t_no_agent(),
        t_clean_speech(),
        t_truncate(),
    ]

    if args.live:
        results.append(t_live_agent())

    print("\n" + "=" * 42)
    passed = sum(1 for r in results if r)
    print(f"结果：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
