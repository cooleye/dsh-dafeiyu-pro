#!/usr/bin/env python3
"""Phase 0 - 唤醒词 PoC

最小验证：麦克风 → KWS → 是否命中「大肥鱼」。

不依赖 DSH、不依赖 Qt，只要 sherpa-onnx 和 sounddevice。

用法：
    python phase0_kws_poc.py              # 实时监听
    python phase0_kws_poc.py --list-devices   # 列出音频设备
    python phase0_kws_poc.py --wav test.wav   # 用 WAV 文件测试（不用麦克风）
    python phase0_kws_poc.py --selftest       # 不带麦克风的逻辑自检
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
import wave
from pathlib import Path

# ---------- 模型路径 ----------

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = HERE / "kws-model"


def model_files(model_dir: Path) -> dict[str, Path]:
    """确认五个必需文件都在，缺哪个直接说清楚。"""
    required = {
        "encoder": model_dir / "kws_encoder.onnx",
        "decoder": model_dir / "kws_decoder.onnx",
        "joiner": model_dir / "kws_joiner.onnx",
        "tokens": model_dir / "tokens.txt",
        "keywords": model_dir / "keywords.txt",
    }
    missing = [str(p) for p in required.values() if not p.exists()]
    if missing:
        raise SystemExit(
            "缺少模型文件：\n  " + "\n  ".join(missing) +
            f"\n\n模型目录：{model_dir}"
        )
    return required


# ---------- 重采样 ----------


def resample_to_16k(samples, src_rate: int):
    """把任意采样率降到 16k。

    为什么必须做：模型要 16k，但绝大多数声卡默认 44.1k / 48k。
    desk-ai 在浏览器里靠 Web Audio 自动重采样，我们这边得自己做。

    优先用 numpy 的多相滤波（质量好），退而求其次用线性插值。
    """
    if src_rate == 16000:
        return list(samples)

    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    n_out = int(round(len(arr) * 16000 / src_rate))

    # 简单但够用的做法：线性插值
    # 更高音质可以换 scipy.signal.resample_poly(arr, 16000, src_rate)
    x_old = np.linspace(0, 1, len(arr), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    return np.interp(x_new, x_old, arr).tolist()


# ---------- 创建 KWS ----------


def create_spotter(model_dir: Path, score: float = 3.0, threshold: float = 0.1,
                   max_active_paths: int = 4):
    """创建 KeywordSpotter。

    参数来源：D:\\project\\desk-ai\\src-tauri\\src\\kws.rs
      keywords_score:     3.0   （灵敏度，越高越容易命中）
      keywords_threshold: 0.1   （阈值，越低越容易命中）
      max_active_paths:   4
      num_trailing_blanks: 1

    注意：sherpa-onnx 2.0 的 Python API 是扁平参数，
    不再是 KeywordSpotterConfig 对象（1.x 才是那种写法）。
    """
    import sherpa_onnx

    f = model_files(model_dir)

    spotter = sherpa_onnx.KeywordSpotter(
        tokens=str(f["tokens"]),
        encoder=str(f["encoder"]),
        decoder=str(f["decoder"]),
        joiner=str(f["joiner"]),
        keywords_file=str(f["keywords"]),
        num_threads=1,
        sample_rate=16000,
        feature_dim=80,
        max_active_paths=max_active_paths,
        keywords_score=score,
        keywords_threshold=threshold,
        num_trailing_blanks=1,
        provider="cpu",
    )

    stream = spotter.create_stream()
    return spotter, stream


# ---------- WAV 读取 ----------


def read_wav(path: Path) -> tuple[list[float], int]:
    """读 16-bit PCM WAV，返回 (归一化采样, 采样率)。"""
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise SystemExit(f"只支持 16-bit PCM，实际 {width * 8}-bit：{path}")

    import array

    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()

    # 多声道取第一声道
    if channels > 1:
        samples = samples[::channels]

    return [s / 32768.0 for s in samples], rate


# ---------- 喂音频 ----------


def feed(spotter, stream, samples: list[float], rate: int, verbose: bool = False):
    """喂一段音频，返回命中的关键词（没命中返回 None）。"""
    stream.accept_waveform(rate, samples)

    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        result = spotter.get_result(stream)
        if verbose:
            print(f"    [decode] {result!r}")
        if result:
            keyword = result.strip()
            if keyword:
                print(f"  🎯 命中：{keyword}")
                # 命中后重置，避免同一句话反复触发
                spotter.reset_stream(stream)
                return keyword
    return None


# ---------- 三种模式 ----------


def run_selftest(model_dir: Path) -> int:
    """不碰麦克风的逻辑自检：模型能加载、词表能解析。"""
    print("=== Phase 0 自检 ===\n")

    files = model_files(model_dir)
    print("模型文件：")
    for name, p in files.items():
        size = p.stat().st_size
        print(f"  ✓ {name:9s} {p.name:22s} {size / 1024:8.1f} KB")

    print("\n关键词表：")
    content = files["keywords"].read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.strip():
            print(f"  {line}")

    print("\n加载 sherpa-onnx ...")
    import sherpa_onnx

    print(f"  版本：{getattr(sherpa_onnx, '__version__', 'unknown')}")

    print("创建 KeywordSpotter ...")
    spotter, stream = create_spotter(model_dir)
    print("  ✓ 创建成功")

    # 喂 1 秒静音，确认不崩溃、不误触发
    silence = [0.0] * 16000
    hit = feed(spotter, stream, silence, 16000)
    print(f"  静音 1 秒 → {'误触发！' if hit else '无命中（正确）'}")

    print("\n=== 自检通过 ===")
    return 0


def run_wav(path: Path, model_dir: Path, verbose: bool) -> int:
    """用 WAV 文件测试（不需要麦克风）。"""
    print(f"=== 用 WAV 测试：{path} ===\n")

    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")

    samples, rate = read_wav(path)
    duration = len(samples) / rate
    print(f"音频：{len(samples)} 采样 / {rate} Hz / {duration:.2f} 秒")

    if rate != 16000:
        print(f"⚠️ 采样率是 {rate}，模型要 16000。需要先重采样。")
        print("   可以用 ffmpeg：ffmpeg -i input.wav -ar 16000 -ac 1 output.wav")
        return 1

    spotter, stream = create_spotter(model_dir)
    print("开始喂音频 ...\n")

    t0 = time.time()
    hit = feed(spotter, stream, samples, rate, verbose=verbose)
    elapsed = time.time() - t0

    print(f"\n耗时 {elapsed:.2f} 秒")
    if hit:
        print(f"✅ 命中唤醒词：{hit}")
        return 0
    print("❌ 没有命中")
    return 2


def pick_input_device(preferred: int | None = None) -> tuple[int, int, str]:
    """挑一个能真正打开录音的输入设备。

    返回 (device_index, sample_rate, 说明)。

    为什么需要这个：Windows 上 MME / DirectSound / WASAPI 可能被别的程序
    独占（比如 DSH 的语音输入插件），只有 WDM-KS 是内核流、绕开混音器。
    但 WDM-KS 也不是所有设备都有，所以逐个试。
    """
    import sounddevice as sd

    if preferred is not None:
        dev = sd.query_devices(preferred)
        rate = int(dev["default_samplerate"])
        return preferred, rate, f"手动指定：{dev['name']}"

    candidates = []
    # 按「独占风险从低到高」排序：WDM-KS 最不容易被占用
    host_rank = {"Windows WDM-KS": 0, "Windows WASAPI": 1,
                 "Windows DirectSound": 2, "MME": 3}
    for i, h in enumerate(sd.query_hostapis()):
        di = h.get("default_input_device", -1)
        if di is None or di < 0:
            continue
        dev = sd.query_devices(di)
        rank = host_rank.get(h["name"], 9)
        candidates.append((rank, h["name"], di, int(dev["default_samplerate"]), dev["name"]))

    candidates.sort()

    for rank, host, di, rate, name in candidates:
        try:
            sd.check_input_settings(device=di, samplerate=rate, channels=1, dtype="float32")
            return di, rate, f"{host} / {name[:40]}"
        except Exception:
            continue

    raise SystemExit("找不到可用的录音设备。检查：麦克风是否插好、是否被其他程序独占。")


def run_eval(model_dir: Path, pos_dir: Path, neg_dir: Path | None,
             verbose: bool = False) -> int:
    """批量评估：真阳性率 + 假阳性率。

    目录约定：
      pos_dir/   放「说了大肥鱼」的 wav（应该命中）
      neg_dir/   放「不该命中的话」的 wav（比如日常说话、电视声）

    这是 Phase 0 最重要的一步：只看「三次都命中」是不够的，
    还要知道「会不会该不该叫都叫」。

    用法：
        python phase0_kws_poc.py --eval ./samples/pos --neg ./samples/neg
    """
    print("=== 唤醒词批量评估 ===\n")

    pos_files = sorted(pos_dir.glob("*.wav")) if pos_dir.exists() else []
    neg_files = sorted(neg_dir.glob("*.wav")) if neg_dir and neg_dir.exists() else []

    if not pos_files:
        raise SystemExit(f"正样本目录为空或不存在：{pos_dir}")

    spotter, stream = create_spotter(model_dir)

    def evaluate(paths: list[Path]) -> tuple[int, int]:
        hits = 0
        for p in paths:
            samples, rate = read_wav(p)
            if rate != 16000:
                samples = resample_to_16k(samples, rate)
            spotter.reset_stream(stream)
            if feed(spotter, stream, samples, 16000, verbose=verbose):
                hits += 1
        return hits, len(paths)

    print(f"正样本：{len(pos_files)} 个")
    pos_hit, pos_total = evaluate(pos_files)
    tpr = pos_hit / pos_total * 100 if pos_total else 0
    print(f"  → 命中 {pos_hit}/{pos_total}（真阳性率 {tpr:.1f}%）\n")

    if neg_files:
        print(f"负样本：{len(neg_files)} 个")
        neg_hit, neg_total = evaluate(neg_files)
        fpr = neg_hit / neg_total * 100 if neg_total else 0
        print(f"  → 误触发 {neg_hit}/{neg_total}（假阳性率 {fpr:.1f}%）\n")
    else:
        print("（没提供负样本目录，跳过假阳性测试）\n")
        print("  建议：录 5~10 段日常说话，放到负样本目录里再跑一次。\n")

    # 给个结论
    print("=== 结论 ===")
    if tpr >= 80:
        print(f"  ✅ 真阳性率 {tpr:.0f}%，可用")
    elif tpr >= 60:
        print(f"  ⚠️ 真阳性率 {tpr:.0f}%，偏低，建议调高 keywords_score")
    else:
        print(f"  ❌ 真阳性率 {tpr:.0f}%，不可用，检查词表发音")

    return 0


def run_live(model_dir: Path, device: int | None, device_rate: int | None,
             score: float = 3.0, threshold: float = 0.1) -> int:
    """实时麦克风监听。"""
    import sounddevice as sd

    print("=== 实时唤醒词监听 ===\n")

    spotter, stream = create_spotter(model_dir, score=score, threshold=threshold)
    print(f"✓ KWS 就绪（score={score} threshold={threshold}）")

    dev_index, detected_rate, desc = pick_input_device(device)
    sample_rate = device_rate or detected_rate

    native = sample_rate == 16000
    print(f"  输入设备：{desc}")
    print(f"  采样率：{sample_rate} Hz" + ("" if native else " → 重采样到 16000"))
    print("\n说「大肥鱼」试试。Ctrl+C 退出。\n")

    block_size = int(sample_rate * 0.1)  # 100ms
    audio_q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"  [音频状态] {status}", file=sys.stderr)
        audio_q.put(indata.copy())

    hit_count = 0
    t_start = time.time()

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            device=dev_index,
            callback=callback,
        ):
            import numpy as np

            while True:
                block = audio_q.get()
                samples = block.flatten().tolist()
                if not native:
                    samples = resample_to_16k(samples, sample_rate)

                # 实时音量条，方便确认麦克风在工作
                rms = float(np.sqrt(np.mean(np.square(samples)))) if samples else 0.0
                bar = "█" * int(min(rms * 300, 40))
                print(f"\r  音量 {rms:.3f} {bar:<40}", end="", flush=True)

                hit = feed(spotter, stream, samples, 16000)
                if hit:
                    hit_count += 1
                    elapsed = time.time() - t_start
                    print(f"\n  🎯 [{elapsed:7.1f}s] 第 {hit_count} 次唤醒：{hit}\n")
    except KeyboardInterrupt:
        print(f"\n\n=== 统计 ===")
        print(f"运行 {time.time() - t_start:.1f} 秒，命中 {hit_count} 次")
        return 0


def list_devices():
    import sounddevice as sd

    print("=== 音频设备 ===\n")
    print(sd.query_devices())
    print("\n默认输入设备：", sd.query_devices(kind="input")["name"])


# ---------- 入口 ----------


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 0 唤醒词 PoC")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR,
                        help="模型目录（默认 ./kws-model）")
    parser.add_argument("--selftest", action="store_true",
                        help="逻辑自检，不碰麦克风")
    parser.add_argument("--wav", type=Path, help="用 WAV 文件测试")
    parser.add_argument("--verbose", action="store_true", help="打印解码中间结果")
    parser.add_argument("--list-devices", action="store_true", help="列出音频设备")
    parser.add_argument("--device", type=int, default=None, help="指定输入设备编号")
    parser.add_argument("--device-rate", type=int, default=None,
                        help="强制设备采样率（默认自动探测）")
    parser.add_argument("--score", type=float, default=3.0,
                        help="keywords_score，越高越容易命中（默认 3.0）")
    parser.add_argument("--threshold", type=float, default=0.1,
                        help="keywords_threshold，越低越容易命中（默认 0.1）")
    parser.add_argument("--eval", type=Path, metavar="POS_DIR",
                        help="批量评估正样本目录（含 .wav）")
    parser.add_argument("--neg", type=Path, metavar="NEG_DIR",
                        help="配合 --eval：负样本目录（不该命中的音频）")
    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return 0

    if args.selftest:
        return run_selftest(args.model_dir)

    if args.eval:
        return run_eval(args.model_dir, args.eval, args.neg, args.verbose)

    if args.wav:
        return run_wav(args.wav, args.model_dir, args.verbose)

    return run_live(args.model_dir, args.device, args.device_rate,
                    args.score, args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
