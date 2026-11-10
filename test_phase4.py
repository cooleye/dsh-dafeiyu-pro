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
        self.stopped = 0
        self.busy = False
        self.started_at = 0.0

    def say(self, t):
        self.said.append(t)

    def say_fixed(self, t):
        """固定短句（唤醒/退下）：测试里当普通说话处理。"""
        self.said.append(t)
        return True

    def say_audio(self, pcm, rate):
        pass

    def prewarm(self, phrases):
        pass

    def stop(self):
        self.stopped += 1
        self.busy = False

    def reset(self):
        pass

    @property
    def speaking(self):
        return self.busy


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
        self.modes: list[str] = []
        self.delay = 0.05
        self.result = ("ok", "任务结果：这是干完活的产出。")
        self.on_progress = lambda msg: None
        self.cancelled = 0

    def run_async(self, task, on_done, permission_mode="workspace-write"):
        self.calls.append(task)
        self.modes.append(permission_mode)

        def w():
            time.sleep(self.delay)
            on_done(*self.result)

        import threading
        threading.Thread(target=w, daemon=True).start()

    def cancel(self):
        self.cancelled += 1


class StubVoice:
    def __init__(self, *a, **kw):
        self.state = "awake"
        self.speaking = False
        self.wake_reply = "我在"
        self.on_wake_reply = None
        self.left = 0

    def _render_status(self, rms):
        pass

    def _leave_awake(self):
        self.left += 1
        self.state = "idle"

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


def t_permission_grant() -> bool:
    """要写桌面的任务 → 先问 → 说「允许」→ 用完整权限跑。"""
    app = make_app()
    app._on_user_said("帮我写一首诗，保存到桌面")
    asked = any("允许" in s for s in app.speaker.said)
    pending = app._pending_task is not None
    no_task_yet = len(app.agent.calls) == 0

    app._on_user_said("允许")
    wait_idle(app)
    ran = len(app.agent.calls) == 1
    mode = app.agent.modes[0] if app.agent.modes else ""

    ok = asked and pending and no_task_yet and ran and mode == "danger-full-access"
    print(f"\n[权限：允许]  {'✅' if ok else '❌'}")
    print(f"  先问了吗={asked} 挂起={pending} 未提前跑={no_task_yet}")
    print(f"  最终跑={ran} 权限={mode}")
    return ok


def t_permission_deny() -> bool:
    """说「不要」→ 不执行。"""
    app = make_app()
    app._on_user_said("帮我写一首诗，保存到桌面")
    app._on_user_said("不要")
    wait_idle(app)
    ok = len(app.agent.calls) == 0 and app._pending_task is None
    print(f"\n[权限：拒绝]  {'✅' if ok else '❌'}")
    print(f"  跑了 {len(app.agent.calls)} 次（应为 0）")
    return ok


def t_stop_task() -> bool:
    """任务跑着的时候说「停止任务」→ 调 cancel，并复位 agent_busy。"""
    app = make_app(agent_delay=10)   # 让它一直「跑着」
    app._on_user_said("帮我写一个 Python 脚本")
    assert app.agent_busy, "任务应处于运行状态"

    app._on_user_said("停止任务")

    ok = (app.agent.cancelled == 1
          and app.agent_busy is False
          and any("停" in s for s in app.speaker.said))
    print(f"\n[停止任务]  {'✅' if ok else '❌'}")
    print(f"  cancel 次数={app.agent.cancelled} agent_busy={app.agent_busy}")
    print(f"  说了：{app.speaker.said}")
    return ok


def t_busy_reports_elapsed() -> bool:
    """任务跑着时问进度 → 应该告诉你跑了多久，不是干巴巴一句「等着」。"""
    app = make_app(agent_delay=10)
    app._on_user_said("帮我写一个 Python 脚本")
    app._task_started = time.time() - 12   # 假装已经跑了 12 秒

    app._on_user_said("进度怎么样了")

    said = app.speaker.said[-1] if app.speaker.said else ""
    ok = "秒" in said and "停止" in said
    print(f"\n[进度查询]  {'✅' if ok else '❌'}")
    print(f"  说了：{said!r}")
    return ok


def t_stop_variants() -> bool:
    """各种说法都能停。"""
    words = ["停止任务", "取消", "别干了", "停下", "算了别做了"]
    ok = True
    for w in words:
        app = make_app(agent_delay=10)
        app._on_user_said("帮我写一个 Python 脚本")
        app._on_user_said(w)
        hit = app.agent.cancelled == 1
        if not hit:
            print(f"  ❌ 「{w}」没触发停止")
            ok = False
    print(f"\n[停止措辞]  {'✅' if ok else '❌'}  （{len(words)} 种说法）")
    return ok


def t_progress_event() -> bool:
    """进度事件应该传到 events 层。"""
    received = []

    class Collect:
        def __getattr__(self, name):
            def fn(*a, **kw):
                received.append((name, a))
            return fn

    from phase4_assistant import Assistant

    with patch("phase4_assistant.VoiceLoop", lambda *a, **kw: StubVoice()), \
         patch("phase4_assistant.Speaker", FakeSpeaker), \
         patch("phase4_assistant.LLM", FakeLLM), \
         patch("phase4_assistant.Tools", lambda *a, **kw: None), \
         patch("phase4_assistant.DshAgent", lambda *a, **kw: FakeAgent()):
        app = Assistant(api_key="fake", deepseek_key="", events=Collect())

    app.agent.delay = 10
    app._on_user_said("帮我写一个 Python 脚本")
    app.agent.on_progress("调用工具：write")
    app.agent.on_progress("写文件：test.md")

    kinds = [k for k, _ in received]
    # 第二条会被节流，能收到第一条就说明链路通了
    ok = "task_progress" in kinds
    print(f"\n[进度事件]  {'✅' if ok else '❌'}  收到 {kinds}")
    return ok


def t_farewell_detect() -> bool:
    """退下指令的识别：该中的中，不该中的别中。"""
    from phase4_assistant import is_farewell

    should = ["你退下吧", "退下吧", "你退出吧", "退出", "没事了",
              "先这样吧", "拜拜", "再见", "去休息吧", "你可以走了", "推下吧"]
    should_not = ["我不用了那个功能", "你不用解释那么多，继续说",
                  "退下这个词什么意思", "今天天气怎么样", "帮我写个脚本"]

    bad = []
    for s in should:
        if not is_farewell(s):
            bad.append(f"漏判:{s}")
    for s in should_not:
        if is_farewell(s):
            bad.append(f"误判:{s}")

    ok = not bad
    print(f"\n[退下识别]  {'✅' if ok else '❌'}")
    print(f"  应中 {len(should)} 条 / 不应中 {len(should_not)} 条")
    if bad:
        for b in bad:
            print(f"    {b}")
    return ok


def t_farewell_flow() -> bool:
    """说「退下吧」→ 回到待命、不再说话。

    退下那句话现在也走云端音色（缓存过是秒出），
    所以断言落在 speaker 队列上，不再是本地 SAPI。
    """
    from phase4_assistant import FAREWELL_REPLY

    app = make_app()
    app.active = True
    app._on_user_said("退下吧")
    left = app.loop.left
    active = app.active
    said = list(app.speaker.said)

    ok = (left == 1 and active is False and FAREWELL_REPLY in said)
    print(f"\n[退下流程]  {'✅' if ok else '❌'}")
    print(f"  回待命次数={left}  active={active}  念了={said}")
    return ok


def t_farewell_silences_late_reply() -> bool:
    """退下之后，LLM 晚产出也不该再说话。"""
    from phase4_assistant import FAREWELL_REPLY

    app = make_app()
    app.active = True
    app._say("先说话")           # 这会进队列
    before = len(app.speaker.said)

    app._on_user_said("退下吧")

    # 退下后 LLM 线程迟到的产出
    app.thinking = True
    app._say("迟到的回复")

    said = app.speaker.said
    added = said[before:]
    ok = (len(added) == 1 and added[0] == FAREWELL_REPLY
          and "迟到的回复" not in said)
    print(f"\n[退下后静音]  {'✅' if ok else '❌'}")
    print(f"  退下后新增={added}（应只有告别语）")
    return ok


def t_farewell_cancels_task() -> bool:
    """任务跑着时说退下 → 任务取消 + 回待命。"""
    app = make_app(agent_delay=10)
    app.active = True
    app._on_user_said("帮我写一个 Python 脚本")
    assert app.agent_busy

    with patch("phase4_assistant.sapi_speak", lambda t: None):
        app._on_user_said("退下吧")

    ok = (app.agent.cancelled == 1
          and app.agent_busy is False
          and app.active is False
          and app.loop.left == 1)
    print(f"\n[退下时取消任务]  {'✅' if ok else '❌'}")
    print(f"  cancel={app.agent.cancelled} busy={app.agent_busy} "
          f"active={app.active} 待命={app.loop.left}")
    return ok


def t_no_permission_needed() -> bool:
    """普通任务（不写外部文件）→ 不该问，直接用沙箱跑。"""
    app = make_app()
    app._on_user_said("帮我写一个 Python 脚本")
    wait_idle(app)
    asked = any("允许" in s for s in app.speaker.said)
    mode = app.agent.modes[0] if app.agent.modes else ""
    ok = (not asked) and mode == "workspace-write"
    print(f"\n[普通任务不问权限]  {'✅' if ok else '❌'}")
    print(f"  问了吗={asked} 权限={mode}")
    return ok


def t_barge_in() -> bool:
    """播放中检测到音量超阈值 → 立刻停。"""
    from phase3_chat import BARGE_IN_FRAMES, BARGE_IN_GRACE_MS, BARGE_IN_RMS

    app = make_app()
    app.speaker.busy = True
    # 模拟已经开始念了（超过起音保护期）
    app.speaker.started_at = time.time() - (BARGE_IN_GRACE_MS + 100) / 1000

    # 先来一帧小的（不该停）
    app._check_barge_in(0.001)
    not_stopped_small = app.speaker.stopped == 0

    # 连续 BARGE_IN_FRAMES 帧超过阈值 → 应该停
    for _ in range(BARGE_IN_FRAMES):
        app._check_barge_in(BARGE_IN_RMS + 0.02)

    ok = not_stopped_small and app.speaker.stopped == 1
    print(f"\n[打断]  {'✅' if ok else '❌'}")
    print(f"  小音量不停={not_stopped_small} 超阈值停止次数={app.speaker.stopped}")
    return ok


def t_barge_in_grace() -> bool:
    """刚开口的起音保护期内，不该被打断（防自身回传）。"""
    from phase3_chat import BARGE_IN_GRACE_MS

    app = make_app()
    app.speaker.busy = True
    app.speaker.started_at = time.time()  # 刚开口

    for _ in range(5):
        app._check_barge_in(0.5)

    ok = app.speaker.stopped == 0
    print(f"\n[起音保护]  {'✅' if ok else '❌'}")
    print(f"  保护期内停止次数={app.speaker.stopped}（应为 0）")
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
        t_permission_grant(),
        t_permission_deny(),
        t_stop_task(),
        t_busy_reports_elapsed(),
        t_stop_variants(),
        t_progress_event(),
        t_farewell_detect(),
        t_farewell_flow(),
        t_farewell_silences_late_reply(),
        t_farewell_cancels_task(),
        t_no_permission_needed(),
        t_barge_in(),
        t_barge_in_grace(),
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
