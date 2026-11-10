#!/usr/bin/env python3
"""把 dsh-dafeiyu-pro 提交到 Awesome DSH Plugin 列表。

做四件事：
  1. 给仓库加 dsh-plugin topic（收录硬性要求）
  2. fork 列表仓库
  3. 在新分支上添加 data/plugins/cooleye__dsh-dafeiyu-pro.yml
  4. 向上游开 PR

token 从环境变量 GH_TOKEN 读，不落盘。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
TOKEN = os.environ.get("GH_TOKEN", "")
OWNER = "cooleye"
REPO = "dsh-dafeiyu-pro"
UPSTREAM = "awesome-dsh-plugin/awesome-dsh-plugin"
BRANCH = "add-dafeiyu-pro"
ENTRY_PATH = "data/plugins/cooleye__dsh-dafeiyu-pro.yml"

ROOT = Path(__file__).resolve().parent.parent
ENTRY_FILE = ROOT / "docs" / "catalog" / "cooleye__dsh-dafeiyu-pro.yml"

TOPICS = ["dsh-plugin", "deepseek-harness", "dsh", "desktop-pet",
          "voice-assistant", "tts", "speech-recognition"]

PR_BODY = """Adds `cooleye/dsh-dafeiyu-pro`, a voice-enabled fork of the `dsh-dafeiyu` desktop pet.

What it adds over the upstream pet:

- **Wake word**: local keyword spotting (sherpa-onnx zipformer, ~5 MB) — say 大肥鱼 to start talking, hands-free.
- **Speech in and out**: Alibaba DashScope Parafomer streaming ASR + `qwen3-tts-flash` replies, with 12 selectable voices. Fixed phrases (the wake acknowledgement and the sign-off) are synthesised once and cached to disk, so they use the same voice with ~1 ms playback.
- **Subtitles and a level meter**: what the user says and what the pet answers are shown in the existing bubble, and a voice-level waveform is drawn under the pet.
- **Intent routing**: casual chat goes straight to the model, lookups such as weather go through a small tool, and long jobs are handed to the DSH agent with progress reported back by voice.
- **Sign-off**: saying 退下 / 你走吧 and similar returns it to standby until the next wake word.

It keeps the upstream animations, right-click menu, settings card and balance widget.

Things a reviewer should know:

- It is a **fork**, not a new pet: `lib/client.js`, `runtime/helper.py` and the 3444 animation frames come from `dsh-dafeiyu` v0.1.23, which is credited in the README. The voice layer is the new part.
- It **replaces** the upstream package — installing it alongside `dsh-dafeiyu` conflicts over the control port, so the entry says to disable the upstream first.
- It needs a DashScope API key and is **Windows x64 only** (the helper is a PyInstaller binary; it can be rebuilt from source).
- `dsh.bundle` is declared at the repo root alongside `dsh.client`.

Screenshots are declared in the repo's `screenshots.json`.
"""


def req(method: str, path: str, payload=None, *, api: str = API):
    url = path if path.startswith("http") else f"{api}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "dsh-dafeiyu-pro-catalog-submit",
        "Accept": "application/vnd.github+json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        return error.code, detail


def main() -> int:
    if not TOKEN:
        print("❌ 没有 GH_TOKEN")
        return 1
    if not ENTRY_FILE.exists():
        print(f"❌ 找不到条目文件 {ENTRY_FILE}")
        return 1

    # ---------- 1. topics ----------
    status, body = req("PUT", f"/repos/{OWNER}/{REPO}/topics", {"names": TOPICS})
    if status == 200:
        print(f"✅ topic 已设置：{', '.join(body.get('names', []))}")
    else:
        print(f"⚠️ topic 设置失败 {status}：{str(body)[:200]}")

    # ---------- 2. fork ----------
    status, body = req("GET", f"/repos/{OWNER}/{UPSTREAM.split('/')[1]}")
    if status == 200:
        print("✅ fork 已存在，直接用")
    else:
        print("… 正在 fork")
        status, body = req("POST", f"/repos/{UPSTREAM}/forks", {})
        if status not in (202, 200):
            print(f"❌ fork 失败 {status}：{str(body)[:300]}")
            return 1
        for _ in range(20):
            time.sleep(3)
            s2, _b2 = req("GET", f"/repos/{OWNER}/{UPSTREAM.split('/')[1]}")
            if s2 == 200:
                break
        print("✅ fork 完成")

    fork_repo = UPSTREAM.split("/")[1]

    # ---------- 3. 从上游 main 建分支 ----------
    status, body = req("GET", f"/repos/{UPSTREAM}/git/ref/heads/main")
    if status != 200:
        print(f"❌ 取上游 main 失败 {status}：{str(body)[:200]}")
        return 1
    base_sha = body["object"]["sha"]
    print(f"… 上游 main = {base_sha[:10]}")

    status, body = req("POST", f"/repos/{OWNER}/{fork_repo}/git/refs",
                       {"ref": f"refs/heads/{BRANCH}", "sha": base_sha})
    if status == 201:
        print(f"✅ 分支 {BRANCH} 已创建")
    elif status == 422:      # 已存在
        print(f"✅ 分支 {BRANCH} 已存在")
    else:
        print(f"❌ 建分支失败 {status}：{str(body)[:300]}")
        return 1

    # ---------- 4. 写入条目文件 ----------
    content = ENTRY_FILE.read_bytes()
    status, body = req("PUT", f"/repos/{OWNER}/{fork_repo}/contents/{ENTRY_PATH}", {
        "message": "Add cooleye/dsh-dafeiyu-pro",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    })
    if status in (200, 201):
        print(f"✅ 文件已提交：{ENTRY_PATH}")
    else:
        print(f"❌ 写文件失败 {status}：{str(body)[:300]}")
        return 1

    # ---------- 5. 开 PR ----------
    status, body = req("POST", f"/repos/{UPSTREAM}/pulls", {
        "title": "Add cooleye/dsh-dafeiyu-pro",
        "head": f"{OWNER}:{BRANCH}",
        "base": "main",
        "body": PR_BODY,
    })
    if status == 201:
        print(f"\n🎉 PR 已创建：{body['html_url']}")
        print(f"   编号 #{body['number']}")
        return 0
    if status == 422:
        print(f"⚠️ 可能已有同名 PR：{str(body)[:300]}")
        s2, b2 = req("GET", f"/repos/{UPSTREAM}/pulls?head={OWNER}:{BRANCH}&state=all")
        if s2 == 200 and b2:
            print(f"   已有 PR：{b2[0]['html_url']}")
        return 0
    print(f"❌ 开 PR 失败 {status}：{str(body)[:400]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
