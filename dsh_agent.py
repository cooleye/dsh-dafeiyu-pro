#!/usr/bin/env python3
"""把长任务交给 DSH agent 执行。

原理：起一个 `dsh --profile headless "<任务>"` 子进程，等它跑完拿 stdout。

为什么用 headless profile：
  它是 DSH 的精简启动方式，不带 Web UI / HTTP 层，适合一次性任务。
  实测 `dsh --profile headless "只回复四个字：你好世界"` → 「你好世界」。

前置条件：DEEPSEEK_API_KEY 必须在环境里（headless 用 deepseek-official 路由）。
本项目会从 voice_config 自动注入。

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


def find_dsh() -> str:
    """找一个可用的 dsh 命令。"""
    env_path = os.environ.get("DSH_BIN", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    for c in DSH_CMD_CANDIDATES:
        if Path(c).exists():
            return c
    return "dsh"     # 交给 PATH


class DshAgent:
    """跑一次 DSH headless 任务。"""

    def __init__(
        self,
        api_key: str = "",
        profile: str = "headless",
        timeout: float = 300.0,
        on_progress=None,
    ):
        self.api_key = api_key
        self.profile = profile
        self.timeout = timeout
        self.on_progress = on_progress or (lambda msg: None)
        self._proc: subprocess.Popen | None = None

    def run(self, task: str, permission_mode: str = "workspace-write") -> tuple[bool, str]:
        """执行任务，返回 (成功?, 输出文本)。阻塞。

        permission_mode:
            workspace-write  —— 默认，只能写工作区（安全）
            danger-full-access —— 放开整台机器（写桌面必须用这个）
            read-only        —— 只读

        对应 DSH 的 DSH_PERMISSION_MODE 环境变量。
        """
        task = (task or "").strip()
        if not task:
            return False, "任务为空"

        dsh = find_dsh()
        env = os.environ.copy()
        if self.api_key:
            env.setdefault("DEEPSEEK_API_KEY", self.api_key)
            env.setdefault("DEEPSEEK_OFFICIAL_API_KEY", self.api_key)
        env["DSH_PERMISSION_MODE"] = permission_mode

        cmd = [dsh, "--profile", self.profile, task]
        self.on_progress("启动 DSH agent ...")

        t0 = time.time()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                out, err = self._proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.communicate()
                return False, f"超时（{self.timeout:.0f} 秒）"
        except FileNotFoundError:
            return False, "找不到 dsh 命令"
        except Exception as e:
            return False, f"启动失败：{e}"

        elapsed = time.time() - t0
        self.on_progress(f"完成，用时 {elapsed:.1f} 秒")

        out = (out or "").strip()
        err = (err or "").strip()

        # dsh 会把日志打到 stderr，真正的回答在 stdout
        if self._proc.returncode != 0 and not out:
            return False, (err or "执行失败")[-400:]

        return True, out

    def run_async(self, task: str, on_done, permission_mode: str = "workspace-write") -> None:
        """后台跑，完成后回调 on_done(ok, text)。"""
        def worker():
            ok, text = self.run(task, permission_mode=permission_mode)
            try:
                on_done(ok, text)
            except Exception as e:
                print(f"\n  ❌ 回调失败：{e}\n")

        threading.Thread(target=worker, daemon=True).start()

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass


def main() -> int:
    p = argparse.ArgumentParser(description="跑一次 DSH headless 任务")
    p.add_argument("task", type=str)
    p.add_argument("--timeout", type=float, default=300)
    args = p.parse_args()

    from voice_config import load_keys
    keys = load_keys()

    agent = DshAgent(
        api_key=keys.deepseek,
        timeout=args.timeout,
        on_progress=lambda m: print(f"  [{m}]"),
    )

    t0 = time.time()
    ok, text = agent.run(args.task)
    print(f"\n{'✅' if ok else '❌'} 用时 {time.time()-t0:.1f} 秒")
    print(text[:1500])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
