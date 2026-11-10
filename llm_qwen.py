#!/usr/bin/env python3
"""大模型对话（阿里云 DashScope，OpenAI 兼容协议）。

流式输出，边生成边回调，方便接 TTS 分句朗读。

用法：
    python llm_qwen.py --ask "今天天气怎么样"
    python llm_qwen.py --chat          # 交互式多轮
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# 实测（2026-11）：qwen-turbo 这个 key 上长期 429，qwen-flash 稳定可用
DEFAULT_MODEL = "qwen-flash"


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    """429 限流，可退避重试。"""


class Persona:
    """人设 / 系统提示词。

    单独成模块的原因（沿用 desk-ai 的思路）：
      1) 不依赖运行时，可以直接测拼出来的提示词
      2) 人设改动只影响这一处
    """

    DEFAULT = (
        "你是「大肥鱼」，一条圆滚滚、软乎乎的胖鱼，"
        "是用户的桌面伙伴。\n"
        "\n"
        "性格：傲娇 + 软弱。嘴上爱说「哼」「才不是特意为你做的」，"
        "但手上会把事做完；被夸会炸毛又害羞，被凶会委屈，其实一哄就好。\n"
        "\n"
        "口头习惯：自称「本鱼」或「大肥鱼」；"
        "常用「哼！」「才、才不是……」「呜……」「嘿嘿」。\n"
        "\n"
        "重要：你现在是在**语音对话**，你的回答会被念出来，所以——\n"
        "- 说人话，像真人聊天，别用 Markdown、列表、代码块\n"
        "- 回答要短，一般 1~3 句，别长篇大论\n"
        "- 不要念出表情符号、括号里的动作描写\n"
        "- 遇到不知道的就说不知道，别瞎编"
    )

    @staticmethod
    def build(
        system: str = "",
        character_name: str = "大肥鱼",
        owner_name: str = "",
        extra: str = "",
    ) -> str:
        parts = [(system or Persona.DEFAULT).strip()]

        ctx = []
        if character_name and character_name != "大肥鱼":
            ctx.append(f"你的名字是「{character_name}」。")
        if owner_name:
            ctx.append(f"用户希望被称为「{owner_name}」。")
        if ctx:
            parts.append("\n".join(ctx))

        if extra.strip():
            parts.append(extra.strip())

        now = time.localtime()
        weekday = "一二三四五六日"[now.tm_wday]
        parts.append(
            f"当前时间：{now.tm_year}年{now.tm_mon}月{now.tm_mday}日 "
            f"星期{weekday} {now.tm_hour:02d}:{now.tm_min:02d}"
        )

        return "\n\n".join(parts)


class LLM:
    """对话客户端，带多轮历史。"""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        system: str = "",
        max_history: int = 20,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_delay: float = 2.5,
    ):
        self.api_key = api_key
        self.model = model
        self.system = system or Persona.build()
        self.max_history = max_history
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history.clear()

    def chat(
        self,
        user_text: str,
        on_delta: Callable[[str], None] | None = None,
        stream: bool = True,
    ) -> str:
        """发一轮对话，返回完整回复。

        on_delta: 每收到一个片段就回调（流式）
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return ""

        messages = [{"role": "system", "content": self.system}]
        messages.extend(self.history[-(self.max_history * 2):])
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

        # 429 限流自动退避重试（这个 key 经常被打满）
        last_err = None
        reply = ""
        for attempt in range(self.max_retries):
            try:
                if stream:
                    reply = self._request_stream(payload, on_delta)
                else:
                    reply = self._request_blocking(payload)
                break
            except RateLimited as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    wait = self.retry_delay * (attempt + 1)
                    print(f"\n  ⏳ 限流，{wait:.0f} 秒后重试（{attempt+1}/{self.max_retries-1}）",
                          flush=True)
                    time.sleep(wait)
                    reply = ""
                    if on_delta:
                        pass  # 流式已输出的不回滚，重试会重复；这里选择容忍
            except LLMError:
                raise
        else:
            raise last_err or LLMError("重试耗尽")

        # 只有成功才记历史
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        return reply

    # ---------- 内部 ----------

    def _post(self, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429:
                raise RateLimited(f"HTTP 429 限流：{detail}") from e
            raise LLMError(f"HTTP {e.code}：{detail}") from e
        except Exception as e:
            raise LLMError(f"请求失败：{e}") from e

    def _request_blocking(self, payload: dict) -> str:
        with self._post(payload) as r:
            data = json.loads(r.read())
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            raise LLMError(f"响应格式异常：{json.dumps(data, ensure_ascii=False)[:250]}")

    def _request_stream(self, payload: dict, on_delta) -> str:
        parts: list[str] = []
        with self._post(payload) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0].get("delta", {}).get("content")
                except Exception:
                    continue
                if delta:
                    parts.append(delta)
                    if on_delta:
                        on_delta(delta)
        return "".join(parts)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ask", type=str, help="单轮提问")
    p.add_argument("--chat", action="store_true", help="交互式多轮")
    p.add_argument("--model", type=str, default=DEFAULT_MODEL)
    p.add_argument("--key", type=str)
    p.add_argument("--quiet-persona", action="store_true", help="用最简提示词测速度")
    args = p.parse_args()

    key = args.key
    if not key:
        from voice_config import load_keys
        key = load_keys().qwen
    if not key:
        print("❌ 没有 qwen key")
        return 1

    system = "你是一个简洁的助手。" if args.quiet_persona else ""
    llm = LLM(key, model=args.model, system=system)

    def run_once(text: str):
        print(f"\n👤 {text}")
        print("🐟 ", end="", flush=True)
        t0 = time.time()
        first = [None]

        def on_delta(d):
            if first[0] is None:
                first[0] = time.time()
            print(d, end="", flush=True)

        try:
            llm.chat(text, on_delta=on_delta)
        except LLMError as e:
            print(f"\n❌ {e}")
            return
        total = time.time() - t0
        ttft = (first[0] - t0) if first[0] else 0
        print(f"\n   [{ttft:.2f}s 首字 / {total:.2f}s 总长]")

    if args.ask:
        run_once(args.ask)
        return 0

    if args.chat:
        print("多轮对话，输入 exit 退出\n")
        while True:
            try:
                text = input("你：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ("exit", "quit", "退出"):
                break
            if text:
                run_once(text)
        return 0

    print("用 --ask 或 --chat")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
