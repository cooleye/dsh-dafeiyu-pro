#!/usr/bin/env python3
"""音频播放：支持中途打断。

Phase 1 核心组件。设计要点：

1. **能被打断** —— 播放时检测到用户开口要立刻停。
   上游 desk-ai 的教训：打断后要保留 1 秒音频重喂，否则半句话丢了。
2. **可复用** —— 同一个 Player 实例既能播本地 TTS，也能播云端 TTS 流。
3. **线程安全** —— 播放是阻塞的，打断来自另一个线程。

用法：
    python audio_player.py --say "你好，我是大肥鱼"
    python audio_player.py --wav out.wav
"""

from __future__ import annotations

import argparse
import collections
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# 输出设备的选择可以写在文件里，改完重启语音服务就生效（不用重启 DSH）。
#   · 全数字        → 当设备编号
#   · 其它字符串    → 按名字模糊匹配（设备编号会变，名字不会）
#   · 文件不存在/空 → 用系统默认
OUTPUT_DEVICE_FILE = Path(
    os.environ.get("DSH_DAFEIYU_OUTPUT_DEVICE_FILE")
    or str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "dsh-dafeiyu" / "output-device.txt")
)

# 音量：
#   · TARGET_PEAK —— 每次播放前把波形峰值归一到这个值（TTS 原始峰值只有 0.36，
#     不归一的话天生就小声，实测用户反馈「听不大声音」）
#   · MAX_GAIN    —— 最多放大多少倍，防止把噪声底也一起放大
#   · volume.txt  —— 用户再叠一个倍率，改完重启语音服务生效
TARGET_PEAK = 0.95
MAX_GAIN = 3.5
VOLUME_FILE = Path(
    os.environ.get("DSH_DAFEIYU_VOLUME_FILE")
    or str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "dsh-dafeiyu" / "volume.txt")
)


def read_volume_setting() -> float:
    """读用户音量倍率（默认 1.0）。"""
    try:
        text = VOLUME_FILE.read_text(encoding="utf-8-sig").strip()
        return max(0.1, min(5.0, float(text)))
    except (OSError, ValueError):
        return 1.0


def read_output_device_setting() -> str:
    try:
        # ⚠️ 用 PowerShell 的 Set-Content -Encoding UTF8 写会带 BOM，
        #    不剥掉的话名字匹配永远失败（踩过）。
        return OUTPUT_DEVICE_FILE.read_text(encoding="utf-8-sig").strip()
    except (OSError, ValueError):
        return ""


def resolve_output_device(value: int | str | None = None) -> int | None:
    """把「设备编号 / 设备名 / 空」解析成 sounddevice 的设备索引。

    ⚠️ 为什么要能按名字选：
    这台机器上有 30 多个输出设备（蓝牙耳机、HDMI、网易虚拟音频、Senary…），
    编号每次插拔都会变；而且 PortAudio 对没接上的设备也会「播放成功」，
    所以默认设备选错了根本没有任何报错 —— 就是单纯没声音。
    """
    if isinstance(value, int):
        return value

    text = (value or "").strip() or read_output_device_setting()
    if not text:
        return None

    if text.isdigit():
        return int(text)

    lowered = text.lower()
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    for index, device in enumerate(devices):
        if device["max_output_channels"] <= 0:
            continue
        if lowered in device["name"].lower():
            return index
    return None


class Player:
    """可打断的音频播放器。

    用法：
        player = Player()
        player.play_pcm(samples, 16000)   # 阻塞，直到播完或被打断

    另一个线程里调 player.stop() 就立即停止。
    """

    def __init__(self, device: int | str | None = None):
        self.device = resolve_output_device(device)
        self.device_name = self._describe(self.device)
        self._stop_event = threading.Event()
        self._playing = False
        self._lock = threading.Lock()
        # 播放音量曲线：(单调时钟, 该音频块 RMS)。
        # 用途：判断麦克风里的声音是「自己漏出去的」还是「用户真的在说」。
        # 回声的音量会跟着这条曲线走，真人的声音不会。
        self._levels: collections.deque[tuple[float, float]] = collections.deque(maxlen=80)

    @property
    def is_playing(self) -> bool:
        return self._playing

    def level_series(self, since: float) -> list[tuple[float, float]]:
        """取出 since 之后的播放音量采样。"""
        return [(t, r) for t, r in list(self._levels) if t >= since]

    @staticmethod
    def _describe(device: int | None) -> str:
        try:
            if device is None:
                return "系统默认"
            return str(sd.query_devices(device)["name"])
        except Exception:
            return "未知"

    def _open_stream(self, sample_rate: int, block: int):
        """打开输出流；配置的设备打不开就退回系统默认。

        PortAudio 对没接上的设备也可能「成功」但不发声，
        所以这里只兜住真正抛异常的情况（设备被拔掉、被独占）。
        """
        try:
            return sd.OutputStream(samplerate=sample_rate, channels=1,
                                   dtype="float32", device=self.device, blocksize=block)
        except Exception:
            if self.device is None:
                raise
            print(f"  ⚠️ 输出设备「{self.device_name}」打不开，退回系统默认")
            self.device = None
            self.device_name = "系统默认"
            return sd.OutputStream(samplerate=sample_rate, channels=1,
                                   dtype="float32", device=None, blocksize=block)

    @staticmethod
    def _normalise(samples: np.ndarray) -> np.ndarray:
        """把峰值拉到 TARGET_PEAK，再乘用户音量倍率。"""
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        gain = 1.0
        if peak > 0.02:
            gain = min(MAX_GAIN, TARGET_PEAK / peak)
        gain *= read_volume_setting()
        if abs(gain - 1.0) < 1e-3:
            return samples
        return np.clip(samples * gain, -1.0, 1.0)

    def stop(self) -> None:
        """请求停止当前播放。立即返回，实际停止在下一个音频块。"""
        self._stop_event.set()

    def play_pcm(self, samples: np.ndarray, sample_rate: int) -> bool:
        """播放 float32 [-1,1] 的 PCM。

        返回 True = 正常播完，False = 被打断。

        播放前会把峰值归一：TTS 出来的波形峰值只有 0.3 左右，
        不归一就是「在响但听不大」。归一之后再叠用户音量倍率。
        """
        if da := samples.dtype:
            if da != np.float32:
                samples = samples.astype(np.float32)

        if samples.ndim > 1:
            samples = samples.flatten()

        samples = self._normalise(samples)

        self._stop_event.clear()

        # 分块播放，每块约 50ms，这样打断响应够快
        block = max(1, int(sample_rate * 0.05))
        total = len(samples)

        with self._lock:
            self._playing = True
        self._levels.clear()

        try:
            stream = self._open_stream(sample_rate, block)
            with stream:
                pos = 0
                while pos < total:
                    if self._stop_event.is_set():
                        return False
                    chunk = samples[pos:pos + block]
                    # 补零到 block 长度（最后一块可能不满）
                    if len(chunk) < block:
                        chunk = np.pad(chunk, (0, block - len(chunk)))
                    stream.write(chunk.reshape(-1, 1))
                    # 记下这一块的音量（给「回声判定」用）
                    try:
                        level = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
                    except Exception:
                        level = 0.0
                    self._levels.append((time.monotonic(), level))
                    pos += block
            return True
        finally:
            with self._lock:
                self._playing = False

    def play_wav(self, path: Path) -> bool:
        """播一个 16-bit PCM WAV。"""
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            rate = w.getframerate()
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())

        if width != 2:
            raise ValueError(f"只支持 16-bit，实际 {width * 8}-bit")

        import array
        pcm = array.array("h")
        pcm.frombytes(frames)
        if channels > 1:
            pcm = pcm[::channels]

        samples = np.array(pcm, dtype=np.float32) / 32768.0
        return self.play_pcm(samples, rate)


# ---------- Windows SAPI（本地 TTS 兜底） ----------


def sapi_speak(text: str, rate: int = 0, volume: int = 100) -> None:
    """用 Windows SAPI 说话（阻塞）。

    为什么用它做兜底：零下载、零配置，系统自带中文语音。
    缺点是音质机械（Microsoft Huihui），只用来念「我在」「好的」这种短句。

    rate: -10（慢）到 10（快），0 是正常
    """
    import subprocess

    # 用 PowerShell 调 SAPI，避免依赖 pywin32
    # 注意：文本要转义单引号
    safe = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {rate}; $s.Volume = {volume}; "
        f"$s.Speak('{safe}')"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
    )


def sapi_speak_async(text: str, rate: int = 0, volume: int = 100) -> threading.Thread:
    """异步版，不阻塞调用方。"""
    t = threading.Thread(target=sapi_speak, args=(text, rate, volume), daemon=True)
    t.start()
    return t


# ---------- 命令行测试 ----------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--say", type=str, help="用 SAPI 说话")
    p.add_argument("--wav", type=Path, help="播放 WAV 文件")
    p.add_argument("--device", type=int, default=None)
    args = p.parse_args()

    if args.say:
        print(f"SAPI 朗读：{args.say}")
        t0 = time.time()
        sapi_speak(args.say)
        print(f"完成，耗时 {time.time() - t0:.2f} 秒")
        return 0

    if args.wav:
        print(f"播放：{args.wav}")
        player = Player(args.device)
        t0 = time.time()
        ok = player.play_wav(args.wav)
        print(f"{'播完' if ok else '被打断'}，耗时 {time.time() - t0:.2f} 秒")
        return 0

    print("用 --say 或 --wav 指定要播放的内容")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
