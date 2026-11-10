#!/usr/bin/env python3
"""事件出口：把语音助手的内部状态输出到不同地方。

两种实现：
  ConsoleEvents —— 打到终端（命令行模式）
  JsonlEvents   —— 写 JSONL 到 stdout（桌宠进程模式）

为什么要抽出来：
  同一套语音逻辑，既要能在终端里调试，又要能嵌进桌宠。
  把「打印」换成「发事件」，就不用维护两份代码。

事件类型（JSONL 的 kind 字段）：
  ready            服务就绪
  state            状态变化（idle / listening / thinking / speaking / busy）
  user_text        用户说了什么（ASR 结果）
  reply_start      开始生成回复
  delta            回复片段（流式）
  reply_end        回复结束（附统计）
  tool             调用了工具
  task_start       派给 agent 的任务
  task_done        任务结束
  task_progress    任务进度（气泡显示，不念）
  task_cancel      任务被取消
  permission_ask   请求权限
  error            出错
  log              普通日志（终端才显示）
"""

from __future__ import annotations

import json
import sys


class BaseEvents:
    """事件基类：全部空实现，子类挑需要的覆盖。"""

    def ready(self, **info) -> None: ...
    def state(self, name: str, **extra) -> None: ...
    def user_text(self, text: str) -> None: ...
    def reply_start(self) -> None: ...
    def delta(self, text: str) -> None: ...
    def reply_end(self, **stats) -> None: ...
    def tool(self, name: str, args) -> None: ...
    def task_start(self, task: str, granted: bool) -> None: ...
    def task_done(self, ok: bool, text: str) -> None: ...
    def task_progress(self, text: str) -> None: ...
    def task_cancel(self) -> None: ...
    def permission_ask(self) -> None: ...
    def error(self, message: str) -> None: ...
    def log(self, message: str) -> None: ...
    def level(self, value: float, activity: str = "") -> None: ...


class ConsoleEvents(BaseEvents):
    """终端输出（命令行 Demo 用）。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self._mid_line = False

    def _p(self, s: str, **kw) -> None:
        if self.verbose:
            print(s, **kw)

    def ready(self, **info) -> None:
        self._p("=== 分级路由语音助手 ===")
        for k, v in info.items():
            self._p(f"  {k}：{v}")
        self._p("")

    def state(self, name: str, **extra) -> None:
        pass      # 终端靠图标显示，不单独打

    def user_text(self, text: str) -> None:
        self._p(f"\n  💬 你说：{text}")

    def reply_start(self) -> None:
        self._p("\n  🐟 ", end="", flush=True)

    def delta(self, text: str) -> None:
        self._p(text, end="", flush=True)

    def reply_end(self, **stats) -> None:
        ttft = stats.get("ttft", 0)
        total = stats.get("total", 0)
        n = stats.get("sentences", 0)
        tools = stats.get("tools") or []
        extra = f" / 工具 {tools}" if tools else ""
        self._p(f"\n  [{ttft:.2f}s 首字 / {total:.2f}s 生成 / {n} 句{extra}]\n")

    def tool(self, name: str, args) -> None:
        self._p(f"\n  🔧 {name} {str(args)[:60]}", flush=True)

    def task_start(self, task: str, granted: bool) -> None:
        tag = "（完整权限）" if granted else "（工作区沙箱）"
        self._p(f"  🚀 派给 DSH agent{tag}："
                f"{task[:36]}{'…' if len(task) > 36 else ''}")

    def task_done(self, ok: bool, text: str) -> None:
        self._p(f"  {'✅' if ok else '❌'} 任务{'完成' if ok else '失败'}")

    def task_progress(self, text: str) -> None:
        self._p(f"  ⏳ {text}")

    def task_cancel(self) -> None:
        self._p("  🛑 任务已取消")

    def permission_ask(self) -> None:
        self._p("  🔐 这个任务要写文件，先征得同意")

    def error(self, message: str) -> None:
        self._p(f"\n  ❌ {message}\n")

    def log(self, message: str) -> None:
        # 终端模式下日志直接打出来，方便调试
        self._p(f"  {message}")


class JsonlEvents(BaseEvents):
    """JSONL 输出（桌宠进程用）。

    ⚠️ stdout 专供协议使用，任何多余输出都会污染。
       所以 log/error 一律走 stderr。
    """

    def __init__(self, stream=None, err=None):
        self.stream = stream or sys.stdout
        self.err = err or sys.stderr

    def _emit(self, kind: str, **payload) -> None:
        msg = {"kind": kind, **payload}
        try:
            self.stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.stream.flush()
        except Exception:
            pass

    def ready(self, **info) -> None:
        self._emit("ready", **info)

    def state(self, name: str, **extra) -> None:
        self._emit("state", state=name, **extra)

    def user_text(self, text: str) -> None:
        self._emit("user_text", text=text)

    def reply_start(self) -> None:
        self._emit("reply_start")

    def delta(self, text: str) -> None:
        self._emit("delta", text=text)

    def reply_end(self, **stats) -> None:
        self._emit("reply_end", **stats)

    def tool(self, name: str, args) -> None:
        self._emit("tool", name=name, args=str(args)[:200])

    def task_start(self, task: str, granted: bool) -> None:
        self._emit("task_start", task=task, granted=granted)

    def task_done(self, ok: bool, text: str) -> None:
        self._emit("task_done", ok=ok, text=text[:800])

    def task_progress(self, text: str) -> None:
        self._emit("task_progress", text=str(text)[:200])

    def task_cancel(self) -> None:
        self._emit("task_cancel")

    def permission_ask(self) -> None:
        self._emit("permission_ask")

    def error(self, message: str) -> None:
        self._emit("error", message=str(message)[:400])
        print(f"[voice] {message}", file=self.err, flush=True)

    def log(self, message: str) -> None:
        print(f"[voice] {message}", file=self.err, flush=True)

    def level(self, value: float, activity: str = "") -> None:
        """实时音量（0~1），驱动桌宠旁边的声波动画。

        每 100ms 一条，纯数值，非常轻。
        """
        self._emit("level", level=round(float(value), 3), activity=activity)
