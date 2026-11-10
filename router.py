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
    # 产出类（落到文件/代码才算活；纯创作留给 LLM 快速答）
    r"写(一|个|下|篇|份|首|段)?\s*(文档|报告|方案|文章|脚本|程序|代码|函数|类|网页|页面)",
    r"(做|生成|创建|建)(一|个|下)?\s*(文档|报告|表格|图表|网页|脚本|程序|项目|目录|文件)",
    # 代码类
    r"(改|修改|重构|优化|调试|修复|实现|开发|部署)(一下|下)?\s*(这|那|代码|程序|项目|功能|bug|接口|函数)",
    r"把.{0,14}(重构|改一下|改改|优化|修好|修复|拆开|合并|跑通|跑起来)",
    r"(看看|检查|审查)\s*(代码|项目|这个文件|仓库)",
    r"(帮我|给我)\s*(写|改|实现|修复|部署|安装|配置)",
    r"(帮我|给我).{0,12}(重构|改|优化|修复|部署|配置|安装|跑通)",
    # 资料类（要读+整理，不是一句话能答的）
    r"(查|搜|找|整理|总结)\s*(一下|下)?\s*(资料|文档|教程|用法|怎么用|如何实现)",
    r"(做个|做一个|写个|写一份|整理一份)\s*(对比|调研|分析|报告|清单|总结)",
    # 落盘类：明确要求存文件，必然要动文件系统
    r"(保存|存|写|放|导出).{0,8}(到|在).{0,6}(桌面|文件|硬盘|本地|磁盘|电脑)",
    r"(保存|存|导出)到(电脑|本地|桌面)",
]

# ---------- 文件/桌面操作（必须交给 agent）----------
# 这一类最容易出事：模型没有文件系统，如果判成 data 走「带工具聊天」，
# 它会一本正经地说「已经帮你建好/删掉了」——实际上什么都没做。
# 所以只要「动作 + 目标」同时出现，一律判 task。
FILE_TARGETS = (
    r"(文件|文档|文件夹|目录|桌面|桌面上的|txt|docx?|word|excel|xlsx|ppt|pptx|pdf|"
    r"图片|截图|压缩包|exe|word文档|excel表格)"
)
FILE_ACTIONS = (
    r"(创建|新建|建一?个|生成|删除|删掉|删了|删一下|移除|清空|"
    r"重命名|改名|移动|挪到|复制|拷贝|"
    r"写入|写到|写进|保存|存到|存进|导出|下载到|"
    r"打开|读取|读一下|列出|列一下|整理到|扫描)"
)
FILE_ACTION_PATTERNS = [
    rf"{FILE_ACTIONS}.{{0,12}}{FILE_TARGETS}",
    rf"{FILE_TARGETS}.{{0,12}}{FILE_ACTIONS}",
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
    # 让人闭嘴 / 结束对话
    r"^(好了|行了|够了|停|安静|闭嘴|别说了|不用了|不要了)",
    r"(闭嘴|别说了|别念了|安静|停下|不用说了)",
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
    """归一化：去掉语气词和标点。

    为什么需要：ASR 的输出里塞满了「呃」「嗯」和句号，
    比如「你帮我。呃，写一写一个」——
    直接匹配「帮我写」会失败，归一化成「你帮我写一写一个」才匹配得上。
    """
    t = text.strip()
    # 语气词 / 口头禅
    for f in ("呃", "嗯", "啊", "哦", "额", "那个", "就", "这个"):
        t = t.replace(f, "")
    # 标点（保留英文字母数字）
    t = re.sub(r"[\s，。！？、,.!?；;：:…~～\"'“”‘’（）()\[\]【】]", "", t)
    return t


def _match(patterns, text: str):
    """在归一化文本上匹配，返回命中的片段。"""
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m
    return None


def classify(text: str, force: str = "") -> Route:
    """判断意图。

    force: 空 / "chat" / "data" / "task"，强制指定
    """
    raw = _norm(text)
    if not raw:
        return Route("chat", "空输入", 0.0)

    if force in ("chat", "data", "task"):
        return Route(force, "手动指定", 1.0)

    # 1) 聊天优先排除
    m = _match(CHAT_PATTERNS, raw)
    if m:
        # 但如果同时带强任务特征，还是判 task
        tm = _match(TASK_PATTERNS, raw)
        if tm:
            return Route("task", "聊天词+任务词，按任务处理", 0.75, tm.group(0))
        return Route("chat", "命中聊天模式", 0.85, m.group(0))

    # 2) 强任务词
    m = _match(TASK_PATTERNS, raw)
    if m:
        return Route("task", "命中任务模式", 0.85, m.group(0))

    # 2.5) 文件/桌面操作 —— 一定得真动手，不能交给「带工具聊天」
    m = _match(FILE_ACTION_PATTERNS, raw)
    if m:
        return Route("task", "文件/桌面操作", 0.9, m.group(0))

    # 3) 数据类
    m = _match(DATA_PATTERNS, raw)
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
