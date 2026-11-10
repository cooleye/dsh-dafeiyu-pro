#!/usr/bin/env python3
"""Phase 2 状态机测试（不需要麦克风、不联网）。

验证重点：**连续多段语音**不会互相踩踏。

复现的 bug：
    第一段识别完后，第二段音频被喂进已关闭的会话，
    断句时报「还没 start()」，后续彻底不识别。

用法：
    python test_phase2.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class MockASR:
    """假 ASR：记录收到的音频，finish 时返回预设文本。"""

    next_text = "测试文本"
    start_delay = 0.0
    instances: list["MockASR"] = []
    fail_start = False

    def __init__(self, api_key, on_partial=None, on_final=None, **kw):
        self.fed: list[bytes] = []
        self.started = False
        self.closed = False
        MockASR.instances.append(self)

    def start(self):
        if MockASR.start_delay:
            time.sleep(MockASR.start_delay)
        if MockASR.fail_start:
            raise RuntimeError("mock 建连失败")
        self.started = True

    def feed(self, pcm: bytes):
        if not self.started or self.closed:
            raise RuntimeError("还没 start()")
        self.fed.append(pcm)

    def finish(self):
        if not self.started or self.closed:
            raise RuntimeError("还没 start()")
        self.closed = True
        return MockASR.next_text

    def close(self):
        self.closed = True


def make_loop():
    """构造一个 loop，但不连任何真实服务。"""
    from phase2_listen import VoiceLoop

    loop = VoiceLoop(
        api_key="fake",
        model_dir=HERE / "kws-model",
        dialogue_timeout=60,
    )
    loop.native_16k = True
    loop.sample_rate = 16000
    # 测试时不刷进度条，否则输出没法看
    loop._render_status = lambda rms: None
    return loop


def speech_frames(n: int, amplitude: float = 0.2) -> list[np.ndarray]:
    rng = np.random.default_rng(42)
    return [(rng.standard_normal(1600) * amplitude).astype(np.float32)
            for _ in range(n)]


def silence_frames(n: int) -> list[np.ndarray]:
    return [np.zeros(1600, dtype=np.float32) for _ in range(n)]


def run_case(name: str, segments: list[int]) -> bool:
    MockASR.instances.clear()
    MockASR.next_text = f"{name}-文本"

    loop = make_loop()
    loop.state = "awake"
    loop.awake_until = time.time() + 120

    for seg_frames in segments:
        for f in speech_frames(seg_frames):
            loop._tick(f)
        # 静音 12 帧 = 1200ms > 1050ms 阈值 → 断句
        for f in silence_frames(12):
            loop._tick(f)
        time.sleep(0.25)   # 让后台 finish 跑起来
    time.sleep(0.5)

    ok = len(loop.transcripts) == len(segments)

    print(f"\n[{name}]  期望 {len(segments)} 段 / 实际 {len(loop.transcripts)} 段 "
          f"{'✅' if ok else '❌'}")
    for i, t in enumerate(loop.transcripts, 1):
        print(f"    {i}. {t}")

    # 检查有没有残留未关闭的连接
    leaked = [m for m in MockASR.instances if m.started and not m.closed]
    if leaked:
        print(f"    ⚠️ 有 {len(leaked)} 个会话未关闭（可能泄漏）")
        ok = False

    return ok


def run_bad_start_case() -> bool:
    """建连失败时不能崩，也不能卡死。"""
    MockASR.instances.clear()
    MockASR.fail_start = True
    try:
        loop = make_loop()
        loop.state = "awake"
        loop.awake_until = time.time() + 60

        for f in speech_frames(20):
            loop._tick(f)
        for f in silence_frames(12):
            loop._tick(f)
        time.sleep(0.4)

        # 不崩、不识别，就算通过
        ok = len(loop.transcripts) == 0
        print(f"\n[建连失败]  不崩溃、不误识别 {'✅' if ok else '❌'}")
        return ok
    finally:
        MockASR.fail_start = False


def run_stall_case() -> bool:
    """音频回调停止供帧 → 必须抛 AudioStalled，不能把主循环一起拖死。

    复现的 bug：回调里 print 把 stderr 管道写阻塞，
    PortAudio 回调线程永久卡住，主循环死等 aq.get()，
    整个服务假死（进程在、日志不动、CPU 为 0、叫它没反应）。
    """
    import phase2_listen as p2

    old_stall = p2.STREAM_STALL_SECONDS
    p2.STREAM_STALL_SECONDS = 0.3
    try:
        loop = make_loop()
        loop.device, loop.sample_rate, loop.device_desc = 0, 16000, "mock"
        loop.native_16k = True

        class DeadStream:
            """开流成功，但回调永远不触发。"""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(p2.sd, "InputStream", lambda **kw: DeadStream()):
            t0 = time.monotonic()
            try:
                loop._run_stream()
                raised = False
            except p2.AudioStalled:
                raised = True
            elapsed = time.monotonic() - t0

        ok = raised and elapsed < 3.0
        print(f"\n[音频流看门狗]  {'✅' if ok else '❌'}")
        print(f"  抛出 AudioStalled={raised}  用时 {elapsed:.2f}s（应 < 3s）")
        return ok
    finally:
        p2.STREAM_STALL_SECONDS = old_stall


def main() -> int:
    print("=== Phase 2 状态机测试 ===")
    print("（用假 ASR，不联网、不用麦克风）\n")

    results = []

    # 关键：patch 必须覆盖整个测试过程，不只是构造阶段
    with patch("phase2_listen.AliyunASR", MockASR), \
         patch("phase2_listen.pick_input_device", return_value=(0, 16000, "mock")), \
         patch("phase2_listen.create_spotter", return_value=(None, None)):

        results.append(run_case("单段", [20]))
        results.append(run_case("连续三段", [20, 20, 20]))
        results.append(run_case("快速连续五段", [15, 15, 15, 15, 15]))

        # 慢建连
        MockASR.start_delay = 0.25
        results.append(run_case("慢建连 × 两段", [20, 20]))
        MockASR.start_delay = 0.0

        results.append(run_bad_start_case())
        results.append(run_stall_case())

    print("\n" + "=" * 42)
    passed = sum(1 for r in results if r)
    print(f"结果：{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
