#!/usr/bin/env python3
"""意图分流：判断一句话该走哪条路。

三条路：
    chat  ← 闲聊、情绪、玩笑、日常对话        → 直连 LLM
    data  ← 天气、新闻、实时信息、查资料       → LLM + 工具
    task  ← 写文档、改代码、做开发、查资料写报告 → DSH agent

为什么要分流：
    DSH agent 很能干，但一次要几十秒。
    闲聊走它，你说「你好」要等半分钟，体验会崩。
    反过来，写代码走直连 LLM，它没有工具、没有文件系统，做不了。

分流策略（三级）：
    1. 规则命中（0ms）—— 强任务词直接判 task
    2. 小模型分类（可选，300~500ms）
    3. 兜底 -> data（带工具，能自己决定要不要查）

实测建议：先用纯规则，够用就不用小模型（省一次请求）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------- 强任务词 ----------
# 命中这些，基本可以确定是要「干活」而不是聊天。
# 注意：不要用太泛的词（比如「写」单独出现，可能是「写得好」）。
TASK_PATTERNS = [
    # 产出类
    r"写(一|个|下|篇|份)?\s*(文档|报告|方案|文章|脚本|程序|代码|函数|类|网页|页面)",
    r"(做|生成|创建|建)(一|个|下)?\s*(文档|报告|表格|图表|网页|脚本|程序|项目|目录|文件)",
    # 代码类
    r"(改|修改|重构|优化|调试|修复|实现|开发|部署)(一下|下)?\s*(这|那|代码|程序|项目|功能|bug|接口|函数)",
    r"把.{0,14}(重构|改一下|改改|优化|修好|修复|拆开|合并|跑通|跑起来)",
    r"(看看|检查|审查)\s*(代码|项目|这个文件|仓库)",
    r"(帮我|给我)\s*(写|改|实现|修复|部署|安装|配置)",
    r"(帮我|给我).{0,12}(重构|改|优化|修复|部署|配置|安装|跑通)",
    # 资料类（要读+整理，不是一句话能答的）
    r"(查|搜|找|整理|总结)\s*(一下|下)?\s*(资料|文档|教程|用法|怎么用|如何实现)",
    r"(做个|写个|整理一份)\s*(对比|调研|分析|报告|清单)",
]

# ---------- 数据类 ----------
# 需要联网查，但一句话就能答
DATA_PATTERNS = [
    r"天气", r"气温", r"下雨", r"温度",
    r"新闻", r"最近.*(发生|有什么)", r"头条",
    r"(现在|今天|明天|昨天).*(几号|星期几|几点|日期)",
    r"(股价|汇率|油价|金价|票价)",
    r"多少(钱|度|公里|米|斤|克)",
    r"(是谁|是什么|什么意思|怎么读)",
    r"(最新|当前|现在).*(版本|价格|情况|状态)",
]

# ---------- 强制排除 ----------
# 出现这些，说明是聊天/情绪，不该派活
CHAT_PATTERNS = [
    r"^(你好|您好|嗨|hi|hello|早|晚上好|在吗)",
    r"(开心|难过|累|无聊|烦|孤单|郁闷|生气|委屈)",
    r"(喜欢|讨厌|爱|想|觉得).*你",
    r"^(谢谢|多谢|好的|嗯|哦|知道了|收到)",
    r"(陪我|聊|说说话|讲个笑话|唱)",
    # 自我认知/身份类，一句话就能答，不需要工具
    r"(你是谁|你叫什么|你的名字|你是什么|介绍一下你自己|介绍下你自己)",
    r"^(你能做什么|你会什么|你有什么功能)",
]


@dataclass
class Route:
    kind: str          # chat | data | task
    reason: str
    confidence: float  # 0~1
    matched: str = ""

    def __str__(self) -> str:
        return f"{self.kind}（{self.reason}，置信度 {self.confidence:.0%}）"


def _norm(text: str) -> str:
    return text.strip()


def classify(text: str, force: str = "") -> Route:
    """判断意图。

    force: 空 / "chat" / "data" / "task"，强制指定
    """
    text = _norm(text)
    if not text:
        return Route("chat", "空输入", 0.0)

    if force in ("chat", "data", "task"):
        return Route(force, "手动指定", 1.0)

    # 1) 聊天优先排除
    for pat in CHAT_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            # 但如果同时带强任务特征，还是判 task
            for tpat in TASK_PATTERNS:
                tm = re.search(tpat, text)
                if tm:
                    return Route("task", "聊天词+任务词，按任务处理",
                                 0.75, tm.group(0))
            return Route("chat", "命中聊天模式", 0.85, m.group(0))

    # 2) 强任务词
    for pat in TASK_PATTERNS:
        m = re.search(pat, text)
        if m:
            return Route("task", "命中任务模式", 0.85, m.group(0))

    # 3) 数据类
    for pat in DATA_PATTERNS:
        m = re.search(pat, text)
        if m:
            return Route("data", "命中数据模式", 0.8, m.group(0))

    # 4) 兜底：带工具聊天（它自己会决定要不要查）
    return Route("data", "兜底：带工具，让模型自己决定", 0.5)


def describe_routes() -> str:
    lines = ["【任务类】"]
    lines += [f"  {p}" for p in TASK_PATTERNS]
    lines += ["【数据类】"]
    lines += [f"  {p}" for p in DATA_PATTERNS]
    lines += ["【聊天类】"]
    lines += [f"  {p}" for p in CHAT_PATTERNS]
    return "\n".join(lines)


def main() -> int:
    """命令行自测。"""
    import argparse

    p = argparse.ArgumentParser(description="意图分流自测")
    p.add_argument("text", nargs="*", help="要测试的句子（不给就测内置样例）")
    p.add_argument("--list", action="store_true", help="列出所有规则")
    args = p.parse_args()

    if args.list:
        print(describe_routes())
        return 0

    samples = args.text or [
        "你好呀",
        "今天天气怎么样",
        "最近有什么新闻",
        "帮我写一个 Python 脚本，读取 CSV 并统计",
        "帮我把这个项目重构一下",
        "你叫什么名字",
        "我有点累",
        "查一下 pandas 的 merge 怎么用",
        "现在几点了",
        "陪我聊会儿天",
        "帮我做个对比分析",
        "讲个笑话",
    ]

    print(f"{'输入':<32} {'判定':<6} {'置信':>5}  说明")
    print("-" * 78)
    for s in samples:
        r = classify(s)
        print(f"{s:<32} {r.kind:<6} {r.confidence:>5.0%}  {r.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
