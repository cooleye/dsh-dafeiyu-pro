#!/usr/bin/env python3
"""语音服务：给桌宠用的常驻进程。

跟 helper.py 一样的通信方式 —— stdin/stdout 上跑 JSONL。
DSH 插件那边 spawn 它，转发状态；它把语音事件推回去，由插件驱动桌宠动画。

    DSH 插件 (Node)
        ├── helper.py       → Qt 桌宠窗口
        └── voice_service.py → 本文件
                  ↓ stdin  (命令)
                  ↑ stdout (事件)

⚠️ stdout 专供协议，任何多余输出都会污染 → 所有 print 重定向到 stderr。

协议（Node → 服务）：
    {"kind":"start"}                    开始监听
    {"kind":"stop"}                     停止
    {"kind":"config","config":{...}}    更新配置（可热改）
    {"kind":"ds_busy","busy":true}      DSH 正在干活
    {"kind":"shutdown"}                 退出

协议（服务 → Node）：
    {"kind":"ready","version":"..."}
    {"kind":"state","state":"idle|listening|hearing|thinking|speaking|busy"}
    {"kind":"user_text","text":"..."}
    {"kind":"reply_start"}
    {"kind":"delta","text":"..."}
    {"kind":"reply_end","ttft":0.3,"text":"..."}
    {"kind":"tool","name":"web_search"}
    {"kind":"task_start","task":"...","granted":false}
    {"kind":"task_done","ok":true,"text":"..."}
    {"kind":"permission_ask"}
    {"kind":"error","message":"..."}

用法（一般由插件拉起，手动调试也可以）：
    python voice_service.py --stdio
    python voice_service.py --selftest
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VERSION = "0.1.0"


class VoiceService:
    """语音服务的生命周期与协议处理。"""

    def __init__(self, events, config: dict | None = None):
        self.ev = events
        self.config = dict(config or {})
        self.assistant = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._ready_sent = False

    # ---------- 生命周期 ----------

    def start(self) -> bool:
        if self._running:
            self.ev.log("已经在跑了")
            return True

        if not self._build():
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def _build(self) -> bool:
        """按当前配置构造 Assistant。"""
        from phase4_assistant import Assistant
        from voice_config import load_keys

        try:
            keys = load_keys()
        except Exception as e:
            self.ev.error(f"读配置失败：{e}")
            return False

        qwen = self.config.get("qwenKey") or keys.qwen
        if not qwen:
            self.ev.error("没有可用的 qwen API Key（apikey.txt 或配置里都找不到）")
            return False

        kwargs = dict(
            api_key=qwen,
            search_key=self.config.get("searchKey") or keys.bochaai,
            deepseek_key=self.config.get("deepseekKey") or keys.deepseek,
            model_dir=Path(self.config.get("modelDir") or (HERE / "kws-model")),
            voice=self.config.get("voice") or "Cherry",
            llm_model=self.config.get("llmModel") or "qwen-flash",
            device=self.config.get("device"),
            use_agent=bool(self.config.get("useAgent", True)),
            stall_delay=float(self.config.get("stallDelay", 8.0)),
            dialogue_timeout=float(self.config.get("dialogueTimeout", 40.0)),
            ask_permission=bool(self.config.get("askPermission", True)),
            allow_full_access=bool(self.config.get("allowFullAccess", False)),
            events=self.ev,
        )

        try:
            self.assistant = Assistant(**kwargs)
        except Exception as e:
            self.ev.error(f"初始化语音失败：{e}")
            return False

        self.ev.log(
            f"语音服务就绪：模型={kwargs['llm_model']} 音色={kwargs['voice']} "
            f"agent={'开' if kwargs['use_agent'] else '关'}"
        )
        return True

    def _run_loop(self) -> None:
        try:
            self.assistant.run()
        except Exception as e:
            self.ev.error(f"语音循环异常退出：{e}")
        finally:
            self._running = False

    def stop(self) -> None:
        if not self.assistant:
            return
        try:
            self.assistant.speaker.stop()
            self.assistant.loop._leave_awake()
            if self.assistant.agent:
                self.assistant.agent.cancel()
        except Exception:
            pass
        self._running = False
        self.assistant = None
        self.ev.state("idle")
        self.ev.log("已停止")

    def update_config(self, patch: dict) -> None:
        self.config.update(patch or {})
        # 只做了「重启生效」的简化处理：语音循环不容易热改
        self.ev.log(f"配置已更新，重启生效：{list((patch or {}).keys())}")

    def note_ds_busy(self, busy: bool) -> None:
        """DSH 正在干活时，语音别插嘴（避免两边同时说话）。"""
        self.config["_ds_busy"] = bool(busy)

    # ---------- 命令分发 ----------

    def handle(self, msg: dict) -> bool:
        """返回 False 表示要退出。"""
        kind = msg.get("kind")

        if kind == "start":
            self.start()
        elif kind == "stop":
            self.stop()
        elif kind == "config":
            self.update_config(msg.get("config") or {})
        elif kind == "ds_busy":
            self.note_ds_busy(msg.get("busy", False))
        elif kind == "status":
            self.ev.log(f"running={self._running} assistant={bool(self.assistant)}")
        elif kind == "shutdown":
            self.stop()
            return False
        else:
            self.ev.log(f"未知命令：{kind}")
        return True


def run_stdio(config: dict | None = None) -> int:
    """stdin/stdout JSONL 主循环。"""
    from voice_events import JsonlEvents

    # ⚠️ 关键：把 stdout 换成 stderr，让所有杂项 print 都进 stderr，
    #    真 stdout 留给协议。否则桌宠会收到一堆「💬 你说」污染协议。
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    ev = JsonlEvents(stream=real_stdout)
    service = VoiceService(ev, config)

    ev.ready(version=VERSION, python=sys.version.split()[0])

    alive = True

    def reader():
        nonlocal alive
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception as e:
                ev.error(f"命令不是合法 JSON：{e}")
                continue
            try:
                if not service.handle(msg):
                    alive = False
                    break
            except Exception as e:
                ev.error(f"处理命令失败：{e}")

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    # 收到 start 才真正开麦；没收到就一直等着
    try:
        while alive and t.is_alive():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 0


def run_selftest() -> int:
    """不碰麦克风的协议自检。"""
    print("=== 语音服务自检 ===\n")

    from voice_events import JsonlEvents

    import io
    buf = io.StringIO()
    ev = JsonlEvents(stream=buf, err=sys.stderr)

    ev.ready(version=VERSION)
    ev.state("listening")
    ev.user_text("测试")
    ev.reply_start()
    ev.delta("你好")
    ev.reply_end(ttft=0.3, total=0.5, sentences=1, text="你好")
    ev.tool("web_search", '{"query":"x"}')
    ev.task_start("写个文档", False)
    ev.task_done(True, "写好了")
    ev.permission_ask()
    ev.error("模拟错误")

    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    print(f"产出 {len(lines)} 条 JSONL：")
    ok = True
    for l in lines:
        try:
            obj = json.loads(l)
            print(f"  ✓ {obj['kind']:<16} {json.dumps({k: v for k, v in obj.items() if k != 'kind'}, ensure_ascii=False)[:60]}")
        except Exception as e:
            print(f"  ❌ 不是合法 JSON：{l[:60]}（{e}）")
            ok = False

    # 验证命令分发
    print("\n命令分发：")
    svc = VoiceService(ev, {})
    for cmd in ({"kind": "ds_busy", "busy": True},
                {"kind": "config", "config": {"voice": "Serena"}},
                {"kind": "status"},
                {"kind": "unknown_thing"}):
        r = svc.handle(cmd)
        print(f"  {cmd['kind']:<12} → {'继续' if r else '退出'}")

    print(f"\n=== {'通过' if ok else '失败'} ===")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="语音服务（桌宠用）")
    p.add_argument("--stdio", action="store_true", help="JSONL over stdin/stdout")
    p.add_argument("--selftest", action="store_true", help="协议自检，不开麦")
    p.add_argument("--config", type=str, help="JSON 配置字符串")
    args = p.parse_args()

    cfg = {}
    if args.config:
        try:
            cfg = json.loads(args.config)
        except Exception as e:
            print(f"配置不是合法 JSON：{e}", file=sys.stderr)
            return 1

    if args.selftest:
        return run_selftest()
    if args.stdio:
        return run_stdio(cfg)

    print("用 --stdio 或 --selftest")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
