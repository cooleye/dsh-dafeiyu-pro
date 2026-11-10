#!/usr/bin/env python3
"""Phase 4 Demo：分级路由的语音助手

    ┌─────────────────────────────────────────────────────┐
    │  说「大肥鱼」                                        │
    │      ↓                                               │
    │  你说一句话                                          │
    │      ↓                                               │
    │  ┌──────────── 意图分流 ────────────┐                │
    │  ↓              ↓                  ↓                │
    │  闲聊          查数据             长任务              │
    │  直连 LLM      LLM + 搜索工具      DSH agent         │
    │  0.5~1s        1~2s               先垫一句           │
    │                                    「好的，我看看」   │
    │                                    8 秒后补一句       │
    │                                      ↓               │
    │                                  跑完念结果          │
    └─────────────────────────────────────────────────────┘

为什么这么设计：
    闲聊走 agent 要等几十秒，体验会崩；任务走直连 LLM 又没工具做不了。
    分流之后，快的地方快，慢的地方有垫场。

用法：
    python phase4_assistant.py
    python phase4_assistant.py --no-agent      # 关掉 DSH agent（只聊天+搜索）
    python phase4_assistant.py --stall 6       # 第二句垫场的延迟秒数
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dsh_agent import DshAgent  # noqa: E402
from llm_qwen import LLM, LLMError  # noqa: E402
from phase2_listen import VoiceLoop  # noqa: E402
from phase3_chat import (  # noqa: E402
    BARGE_IN_FRAMES,
    BARGE_IN_GRACE_MS,
    BARGE_IN_RMS,
    MAX_BUFFER_CHARS,
    MIN_SENTENCE_CHARS,
    SENTENCE_END,
    Speaker,
    split_long,
)
from router import Route, classify  # noqa: E402
from tools import Tools  # noqa: E402
from voice_config import load_keys  # noqa: E402

# 垫场话术（本地 SAPI，秒回）
STALL_1 = "好的，我看看"
STALL_2 = "这个要花点时间，你先忙别的"
STALL_DELAY = 8.0

# ---------- 权限确认 ----------
# DSH agent 默认跑在 workspace-write 沙箱里，写不了桌面。
# 要写桌面得用 DSH_PERMISSION_MODE=danger-full-access —— 但这等于放开整台机器。
# 所以：默认不放开，任务前问一句，只有明确说「允许」才升权。
PERMISSION_ASK = "这个任务可能要写文件，允许我操作吗？说允许，或者说不"
PERMISSION_OK_WORDS = ["允许", "可以", "好的", "行", "同意", "准了", "授权"]
PERMISSION_NO_WORDS = ["不", "不行", "拒绝", "别", "不要", "算了"]
PERMISSION_TIMEOUT = 12.0

# 念之前把结果里的 Markdown 清掉
MD_PATTERNS = [
    (re.compile(r"```[\s\S]*?```"), ""),          # 代码块
    (re.compile(r"`([^`]*)`"), r"\1"),            # 行内代码
    (re.compile(r"^\s{0,3}#{1,6}\s+", re.M), ""),  # 标题
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),      # 粗体
    (re.compile(r"\*([^*]*)\*"), r"\1"),          # 斜体
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),      # 列表
    (re.compile(r"^\s*\d+\.\s+", re.M), ""),      # 有序列表
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),  # 链接
    (re.compile(r"^\s*\|.*\|\s*$", re.M), ""),    # 表格
]

# 念结果时最多念多少字（完整结果太长了不念）
MAX_SPEAK_CHARS = 220


def clean_for_speech(text: str) -> str:
    """把 Markdown / 代码之类不适合念的东西去掉。"""
    out = text or ""
    for pat, repl in MD_PATTERNS:
        out = pat.sub(repl, out)
    out = re.sub(r"\n{2,}", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = out.strip()

    if len(out) > MAX_SPEAK_CHARS:
        # 尽量在句号处截断
        cut = out[:MAX_SPEAK_CHARS]
        for sep in "。！？!?":
            idx = cut.rfind(sep)
            if idx > MAX_SPEAK_CHARS * 0.5:
                return cut[:idx + 1]
        return cut + "……"
    return out


class Assistant:
    """带分流的语音助手。"""

    def __init__(
        self,
        api_key: str,
        search_key: str = "",
        deepseek_key: str = "",
        model_dir: Path = HERE / "kws-model",
        voice: str = "Cherry",
        llm_model: str = "qwen-flash",
        device: int | None = None,
        use_agent: bool = True,
        stall_delay: float = STALL_DELAY,
        dialogue_timeout: float = 40.0,
        ask_permission: bool = True,
        allow_full_access: bool = False,
    ):
        self.llm = LLM(api_key, model=llm_model)
        self.tools = Tools(search_key)
        self.speaker = Speaker(api_key, voice)
        self.use_agent = use_agent
        self.stall_delay = stall_delay

        self.agent = DshAgent(api_key=deepseek_key) if use_agent else None
        self.agent_busy = False

        self.thinking = False
        self.current_route: Route | None = None
        self._loud_frames = 0

        # 权限
        self.ask_permission = ask_permission
        self.allow_full_access = allow_full_access
        self._pending_task: str | None = None
        self._perm_deadline = 0.0

        self.loop = VoiceLoop(
            api_key=api_key,
            model_dir=model_dir,
            wake_reply="我在",
            device=device,
            dialogue_timeout=dialogue_timeout,
            on_transcript=self._on_user_said,
        )
        self.loop.on_voice_level = self._check_barge_in
        self.loop._render_status = self._render

    # ---------- 打断 ----------

    def _check_barge_in(self, rms: float) -> None:
        """播放期间一开口就闭嘴（不等 ASR）。"""
        if not self.speaker.busy:
            self._loud_frames = 0
            return
        if (time.time() - self.speaker.started_at) * 1000 < BARGE_IN_GRACE_MS:
            return
        if rms > BARGE_IN_RMS:
            self._loud_frames += 1
            if self._loud_frames >= BARGE_IN_FRAMES:
                print(f"\n  🤐 打断（音量 {rms:.3f}），停止播报\n")
                self.speaker.stop()
                self._loud_frames = 0
        else:
            self._loud_frames = 0

    # ---------- 主流程 ----------

    def run(self) -> None:
        print("=== 分级路由语音助手 ===\n")
        print(f"  LLM：{self.llm.model}")
        print(f"  音色：{self.speaker.voice}")
        print(f"  DSH agent：{'开' if self.use_agent else '关'}")
        print(f"  垫场：{self.stall_delay:.0f} 秒后补第二句")
        print("\n说「大肥鱼」叫醒它。它会自动判断该聊还是该干活。")
        print("Ctrl+C 退出。\n")
        self.loop.run()

    # ---------- 收到一句话 ----------

    def _on_user_said(self, text: str) -> None:
        if self.speaker.speaking:
            print("  🤐 打断，停止播报")
            self.speaker.stop()
        self.speaker.reset()

        # ① 正在等权限答复 → 这句话当回答处理，不派活
        if self._pending_task is not None:
            self._handle_permission_reply(text)
            return

        if self.agent_busy:
            print("  ⏳ 之前的任务还在跑")
            self.speaker.say("我还在忙上一个任务，等一下再说")
            return

        route = classify(text)
        self.current_route = route
        print(f"  🧭 分流：{route}")

        if route.kind == "task" and self.use_agent:
            self._do_task(text, route)
        else:
            self._do_chat(text, route)

    # ---------- 权限确认 ----------

    def _handle_permission_reply(self, text: str) -> None:
        """用户对「允许吗」的答复。"""
        task = self._pending_task
        self._pending_task = None
        low = text.strip()

        if any(w in low for w in PERMISSION_NO_WORDS) and not any(
                w in low for w in PERMISSION_OK_WORDS):
            print("  🚫 用户拒绝")
            self.speaker.say("好，那我不动了")
            return

        if any(w in low for w in PERMISSION_OK_WORDS):
            print("  ✅ 用户授权，以完整权限重跑")
            self.speaker.say("好，那我动手了")
            self._run_task(task or "", granted=True)
            return

        # 既不是同意也不是拒绝 → 当没听清，再问一次（但不再无限循环）
        print(f"  ❓ 没听清授权答复：{low}")
        self.speaker.say("我没听清，你说允许还是不允许")
        self._pending_task = task
        self._perm_deadline = time.time() + PERMISSION_TIMEOUT

    def _needs_permission(self, task: str) -> bool:
        """判断这个任务要不要升权。

        命中「写文件到工作区外」的特征才问，否则用默认沙箱直接跑。
        误判的代价：多问一句；漏判的代价：任务失败——所以宁可多问。
        """
        if self.allow_full_access or not self.ask_permission:
            return False

        write_hints = ["保存", "写到", "存到", "放到", "创建一个", "新建", "生成一个文件",
                       "导出", "下载到", "改一下这个文件"]
        outside_hints = ["桌面", "desktop", "文档", "下载", "图片", "D:", "C:",
                         "我的电脑", "文件夹"]
        has_write = any(w in task for w in write_hints)
        has_outside = any(w in task.lower() if w.isascii() else w in task
                          for w in outside_hints)
        return has_write and has_outside

    # ---------- 路径 A/B：直连 LLM（可带工具） ----------

    def _do_chat(self, text: str, route: Route) -> None:
        self.thinking = True

        def work():
            t0 = time.time()
            first = [None]
            buf = ""
            spoken: list[str] = []
            used: list[str] = []

            def flush(sent: str):
                s = sent.strip()
                if len(s) >= MIN_SENTENCE_CHARS:
                    self.speaker.say(s)
                    spoken.append(s)

            def on_delta(d):
                nonlocal buf
                if first[0] is None:
                    first[0] = time.time()
                print(d, end="", flush=True)
                buf += d
                while True:
                    m = SENTENCE_END.search(buf)
                    if not m:
                        break
                    flush(buf[:m.start()])
                    buf = buf[m.end():]
                while len(buf) >= MAX_BUFFER_CHARS:
                    head, buf = split_long(buf)
                    flush(head)

            def on_tool(name, args):
                print(f"\n  🔧 {name} {str(args)[:60]}", flush=True)

            try:
                print("\n  🐟 ", end="", flush=True)
                if route.kind == "data":
                    reply, used = self.llm.chat_with_tools(
                        text, self.tools, on_delta=on_delta, on_tool=on_tool
                    )
                else:
                    reply = self.llm.chat(text, on_delta=on_delta)

                flush(buf)
                ttft = (first[0] - t0) if first[0] else 0
                print(f"\n  [{ttft:.2f}s 首字 / {time.time()-t0:.2f}s 生成"
                      f" / {len(spoken)} 句"
                      + (f" / 工具 {used}" if used else "") + "]\n")
                if not reply.strip():
                    self.speaker.say("唔，我没想好说什么")
            except LLMError as e:
                print(f"\n  ❌ 回复失败：{e}\n")
                self.speaker.say("呜……我这边卡住了")
            finally:
                self.thinking = False

        threading.Thread(target=work, daemon=True).start()

    # ---------- 路径 C：DSH agent ----------

    def _do_task(self, text: str, route: Route) -> None:
        # 需要写工作区外的文件 → 先问一句
        if self._needs_permission(text):
            print(f"  🔐 这个任务要写文件，先征得同意")
            self.speaker.say(PERMISSION_ASK)
            self._pending_task = text
            self._perm_deadline = time.time() + PERMISSION_TIMEOUT
            # 超时视为拒绝
            def timeout_check():
                time.sleep(PERMISSION_TIMEOUT + 0.5)
                if self._pending_task == text:
                    self._pending_task = None
                    print("  ⏱ 权限确认超时，按拒绝处理")
                    self.speaker.say("没等到你答复，那我先不动了")
            threading.Thread(target=timeout_check, daemon=True).start()
            return

        self._run_task(text, granted=False)

    def _run_task(self, text: str, granted: bool) -> None:
        self.agent_busy = True
        t0 = time.time()

        # 第一句垫场：本地秒回
        print(f"  🔊 垫场1：「{STALL_1}」")
        self.speaker.say(STALL_1)

        second_spoken = [False]

        def stall_timer():
            time.sleep(self.stall_delay)
            if self.agent_busy and not second_spoken[0]:
                second_spoken[0] = True
                print(f"  🔊 垫场2：「{STALL_2}」")
                self.speaker.say(STALL_2)

        threading.Thread(target=stall_timer, daemon=True).start()

        def on_done(ok: bool, output: str):
            elapsed = time.time() - t0
            self.agent_busy = False
            print(f"\n  {'✅' if ok else '❌'} 任务{'完成' if ok else '失败'}"
                  f"，{elapsed:.1f} 秒\n")

            if ok:
                say = clean_for_speech(output)
                if not say:
                    say = "做完了，但没给我内容"
                print(f"  📄 要念的内容：{say[:120]}…\n")
                self.speaker.say(say)
            else:
                print(f"  错误：{output[:200]}")
                self.speaker.say("呜……这个任务没做成")

        print(f"  🚀 派给 DSH agent"
              f"{'（完整权限）' if granted else '（工作区沙箱）'}："
              f"{text[:36]}{'…' if len(text)>36 else ''}")
        self.agent.run_async(text, on_done, permission_mode=(
            "danger-full-access" if granted else "workspace-write"
        ))

    # ---------- 界面 ----------

    def _render(self, rms: float) -> None:
        if self.agent_busy:
            icon = "⚙️"
        elif self.thinking:
            icon = "🤔"
        elif self.speaker.busy:
            icon = "🗣"
        elif self.loop.speaking:
            icon = "🔴"
        elif self.loop.state == "awake":
            icon = "👂"
        else:
            icon = "💤"
        bar = "█" * int(min(rms * 200, 20))
        print(f"\r {icon} {rms:.3f} {bar:<20}", end="", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description="分级路由语音助手")
    p.add_argument("--model-dir", type=Path, default=HERE / "kws-model")
    p.add_argument("--voice", type=str, default="Cherry")
    p.add_argument("--llm", type=str, default="qwen-flash")
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--no-agent", action="store_true", help="关掉 DSH agent")
    p.add_argument("--stall", type=float, default=STALL_DELAY,
                   help="第二句垫场的延迟秒数")
    p.add_argument("--timeout", type=float, default=40.0,
                   help="唤醒后无语音的退回时间")
    p.add_argument("--trusted", action="store_true",
                   help="信任模式：不问权限，任务一律给完整权限（谨慎）")
    p.add_argument("--no-ask", action="store_true",
                   help="不询问权限，任务一律用工作区沙箱（写桌面会失败）")
    args = p.parse_args()

    keys = load_keys()
    if not keys.qwen:
        print("❌ 没找到 qwen API Key")
        return 1

    if args.no_agent and not keys.bochaai:
        print("⚠️ 没有搜索 key，data 类问题会靠模型自己编")

    app = Assistant(
        api_key=keys.qwen,
        search_key=keys.bochaai,
        deepseek_key=keys.deepseek,
        model_dir=args.model_dir,
        voice=args.voice,
        llm_model=args.llm,
        device=args.device,
        use_agent=not args.no_agent,
        stall_delay=args.stall,
        dialogue_timeout=args.timeout,
        ask_permission=not args.no_ask,
        allow_full_access=args.trusted,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
