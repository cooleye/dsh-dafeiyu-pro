#!/usr/bin/env python3
"""把长任务交给 DSH agent 执行。

原理：起一个 `dsh --profile headless "<任务>"` 子进程，等它跑完拿 stdout。

为什么用 headless profile：
  它是 DSH 的精简启动方式，不带 Web UI / HTTP 层，适合一次性任务。
  实测 `dsh --profile headless "只回复四个字：你好世界"` → 「你好世界」。

⚠️ 三个必须注意的坑（都踩过）：

1. **必须 stdin=DEVNULL**
   不指定的话子进程会继承父进程的 stdin。语音服务的 stdin 是插件发命令的
   JSONL 管道 —— dsh 去读它就会把命令吃掉，或者一直阻塞等输入。
   表现：任务永远跑不完，而且「停止」命令也发不进去。

2. **必须按进程树杀**
   dsh 会 spawn 自己的子进程（node/工具），只 kill 父进程会留孤儿。
   Windows 上用 taskkill /T /F。

3. **stderr 要实时读**
   不读的话管道写满会阻塞子进程。顺便把 stderr 当进度源用。

用法：
    python dsh_agent.py "帮我列三个 Python 异步编程的注意事项"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# DSH CLI 的固定位置（DeepSeek Harness 桌面版自带）
DSH_CMD_CANDIDATES = [
    r"D:\Program Files (x86)\deepseek\resources\runtime\cli\bin\dsh.cmd",
    "dsh",
]

# agent 一次能干不少活，而且它自己会做校验（有时还会用 Word 打开看一眼），
# 180 秒实测不够：查新闻 + 写 docx 那次跑到 180 秒被我们杀了，
# 但文件其实已经写好了 —— 白报一次失败。
DEFAULT_TIMEOUT = 300.0


def find_dsh() -> str:
    """找一个可用的 dsh 命令。"""
    env_path = os.environ.get("DSH_BIN", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    for c in DSH_CMD_CANDIDATES:
        if Path(c).exists():
            return c
    return "dsh"     # 交给 PATH


def kill_tree(pid: int) -> None:
    """连子进程一起杀掉。

    dsh 会 spawn node / 工具子进程，只 kill 父进程会留孤儿占着文件。
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        # 退路：直接 kill
        try:
            os.kill(pid, 9)
        except Exception:
            pass


class DshAgent:
    """跑一次 DSH headless 任务。"""

    def __init__(
        self,
        api_key: str = "",
        profile: str = "headless",
        timeout: float = DEFAULT_TIMEOUT,
        on_progress=None,
    ):
        self.api_key = api_key
        self.profile = profile
        self.timeout = timeout
        self.on_progress = on_progress or (lambda msg: None)
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._stderr_lines: list[str] = []

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def cancel(self) -> None:
        """取消：杀进程树。"""
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            kill_tree(self._proc.pid)
            self.on_progress("已取消")

    def run(self, task: str, permission_mode: str = "workspace-write") -> tuple[bool, str]:
        """执行任务，返回 (成功?, 输出文本)。阻塞。

        permission_mode:
            workspace-write     —— 默认，只能写工作区（安全）
            danger-full-access  —— 放开整台机器（写桌面必须用这个）
            read-only           —— 只读
        """
        task = (task or "").strip()
        if not task:
            return False, "任务为空"

        self._cancelled = False
        self._stderr_lines = []

        dsh = find_dsh()
        env = os.environ.copy()
        if self.api_key:
            env.setdefault("DEEPSEEK_API_KEY", self.api_key)
            env.setdefault("DEEPSEEK_OFFICIAL_API_KEY", self.api_key)
        env["DSH_PERMISSION_MODE"] = permission_mode

        cmd = [dsh, "--profile", self.profile, task]
        self.on_progress("启动 agent")

        t0 = time.time()
        try:
            self._proc = subprocess.Popen(
                cmd,
                # ⭐ 关键：不能继承父进程的 stdin（那是插件的命令管道）
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            return False, "找不到 dsh 命令"
        except Exception as e:
            return False, f"启动失败：{e}"

        # 起线程实时读 stderr（既防管道写满阻塞，又拿来做进度）
        stdout_buf: list[str] = []

        def read_stderr():
            try:
                for line in self._proc.stderr:
                    line = line.rstrip()
                    if not line:
                        continue
                    self._stderr_lines.append(line)
                    if len(self._stderr_lines) > 200:
                        del self._stderr_lines[:-200]
                    self._emit_progress(line)
            except Exception:
                pass

        def read_stdout():
            try:
                for line in self._proc.stdout:
                    stdout_buf.append(line)
            except Exception:
                pass

        t_err = threading.Thread(target=read_stderr, daemon=True)
        t_out = threading.Thread(target=read_stdout, daemon=True)
        t_err.start()
        t_out.start()

        try:
            self._proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            kill_tree(self._proc.pid)
            try:
                self._proc.wait(timeout=5)
            except Exception:
                pass
            t_err.join(timeout=2)
            t_out.join(timeout=2)
            self._proc = None
            return False, (
                f"跑超过 {self.timeout:.0f} 秒了，我已经把它停了。"
                "要是它已经在收尾，文件可能其实已经生成了——先去看看再说。"
            )

        t_err.join(timeout=3)
        t_out.join(timeout=3)

        elapsed = time.time() - t0
        code = self._proc.returncode
        self._proc = None

        if self._cancelled:
            return False, "已取消"

        out = "".join(stdout_buf).strip()
        err = "\n".join(self._stderr_lines).strip()
        self.on_progress(f"结束，{elapsed:.1f} 秒，退出码 {code}")

        if code != 0 and not out:
            tail = err[-400:] if err else "（无输出）"
            return False, tail

        return True, out

    def _emit_progress(self, line: str) -> None:
        """把 stderr 的一行转成进度描述。

        dsh 的 stderr 是日志，我们不直接把原文塞给用户，
        而是挑出有意义的信息（工具调用、阶段变化）。
        """
        text = line.strip()
        low = text.lower()

        # 过滤掉噪音
        if any(k in low for k in ("deprecat", "warning:", "experimental")):
            return

        tag = "干活中"
        if "tool" in low and ("call" in low or "invoke" in low):
            tag = "调用工具"
        elif "reasoning" in low or "thinking" in low:
            tag = "思考中"
        elif "write" in low or "created" in low or "saved" in low:
            tag = "写文件"
        elif "error" in low or "fail" in low:
            tag = "出错"

        # 只取有内容的短行
        if 4 <= len(text) <= 100:
            self.on_progress(f"{tag}：{text}")

    def run_async(self, task: str, on_done, permission_mode: str = "workspace-write") -> None:
        """后台跑，完成后回调 on_done(ok, text)。"""
        def worker():
            ok, text = self.run(task, permission_mode=permission_mode)
            try:
                on_done(ok, text)
            except Exception as e:
                print(f"\n  ❌ 回调失败：{e}\n")

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    p = argparse.ArgumentParser(description="跑一次 DSH headless 任务")
    p.add_argument("task", type=str)
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("--full", action="store_true", help="给完整权限")
    args = p.parse_args()

    from voice_config import load_keys
    keys = load_keys()

    agent = DshAgent(
        api_key=keys.deepseek,
        timeout=args.timeout,
        on_progress=lambda m: print(f"  [{m}]"),
    )

    t0 = time.time()
    ok, text = agent.run(
        args.task,
        permission_mode="danger-full-access" if args.full else "workspace-write",
    )
    print(f"\n{'✅' if ok else '❌'} 用时 {time.time()-t0:.1f} 秒")
    print(text[:1500])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
