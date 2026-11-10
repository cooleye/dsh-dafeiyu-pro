#!/usr/bin/env python3
"""配置加载：从多个来源读 API Key。

优先级（从高到低）：
  1. 环境变量
  2. 项目根目录的 apikey.txt（兼容 desk-ai 格式）
  3. %USERPROFILE%\\.dsh\\voice-keys.json

apikey.txt 格式（冒号分隔，每行一个）：
    qwen:sk-xxxxxxxx
    minimax:sk-api-xxxxxxxx
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass
class Keys:
    qwen: str = ""
    minimax: str = ""
    groupid: str = ""
    deepseek: str = ""
    bochaai: str = ""
    _sources: dict = field(default_factory=dict)

    def has(self, name: str) -> bool:
        return bool(getattr(self, name, ""))

    def masked(self) -> dict[str, str]:
        """打码后的摘要，用于打印。"""
        out = {}
        for k in ("qwen", "minimax", "deepseek", "bochaai"):
            v = getattr(self, k, "")
            if v:
                out[k] = f"{v[:8]}...{v[-4:]}({len(v)})"
            else:
                out[k] = "（未配置）"
        return out


def _parse_apikey_file(path: Path) -> dict[str, str]:
    """解析 `key:value` 或 `key=value` 格式的文本。"""
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    out = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_]+)\s*[:=]\s*(.+)$", line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def load_keys(extra_path: Path | None = None) -> Keys:
    """按优先级加载配置。"""
    k = Keys()

    # 3) 用户家目录的 json（优先级最低，先读）
    home_file = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".dsh" / "voice-keys.json"
    if home_file.exists():
        try:
            data = json.loads(home_file.read_text(encoding="utf-8"))
            for name, val in data.items():
                if hasattr(k, name) and isinstance(val, str):
                    setattr(k, name, val)
                    k._sources[name] = str(home_file)
        except Exception:
            pass

    # 2) 项目目录 / 指定路径的 apikey.txt
    candidates = [HERE / "apikey.txt", Path(r"D:\project\desk-ai\apikey.txt")]
    if extra_path:
        candidates.insert(0, extra_path)

    for p in candidates:
        parsed = _parse_apikey_file(p)
        for name, val in parsed.items():
            if hasattr(k, name) and val:
                setattr(k, name, val)
                k._sources[name] = str(p)

    # 1) 环境变量（最高优先级）
    env_map = {
        "qwen": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        "minimax": ("MINIMAX_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "bochaai": ("BOCHAAI_API_KEY",),
    }
    for name, vars_ in env_map.items():
        for v in vars_:
            val = os.environ.get(v, "").strip()
            if val:
                setattr(k, name, val)
                k._sources[name] = f"环境变量 {v}"
                break

    return k


def main() -> int:
    k = load_keys()
    print("=== 配置检查 ===\n")
    masked = k.masked()
    for name, val in masked.items():
        src = k._sources.get(name, "")
        src_text = f"  ← {Path(src).name}" if src and "环境" not in src else (f"  ← {src}" if src else "")
        print(f"  {name:10s} {val}{src_text}")
    print()

    if k.has("qwen"):
        print("✅ qwen key 已配置（DashScope）")
        print("   可用于：ASR (paraformer-realtime) / TTS (qwen-tts) / 意图分类")
    else:
        print("❌ 没找到 qwen key")
        print("   需要：在 apikey.txt 里加一行  qwen:sk-xxx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
