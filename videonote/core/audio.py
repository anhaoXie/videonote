# -*- coding: utf-8 -*-
"""音频工具：解码成 wav、读成 numpy、画波形用的降采样。

留在这里是为了让「本地 ASR」和「发音评分」共用同一套读音频的代码。
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from .media import extract_audio


def decode_to_wav(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """任意音频/视频 -> 单声道 16k wav（PyAV 解码，webm/opus/mp4/aac/mp3 都行）。"""
    return extract_audio(src, dst, sample_rate)


def read_wav_float32(path: str | Path, expected_rate: int = 16000):
    """读 16k 单声道 wav -> float32 numpy 数组（取值 -1..1）。

    自己读而不是交给 faster-whisper：它内部用 PyAV 解码，而 PyAV 19 移除了
    `metadata_errors` 参数，直接传文件路径会 TypeError。传 numpy 数组更快也更稳。
    """
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise RuntimeError(f"只支持 16bit wav，当前 {width * 8}bit")
    data = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    audio = data.astype(np.float32) / 32768.0
    if rate != expected_rate:
        n = max(1, int(len(audio) * expected_rate / rate))
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    return audio


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or 1)


def waveform_peaks(path: str | Path, buckets: int = 240) -> List[float]:
    """给前端画波形用的峰值数组（0..1）。"""
    import numpy as np

    audio = read_wav_float32(path)
    if len(audio) == 0:
        return []
    step = max(1, len(audio) // buckets)
    trimmed = audio[: step * buckets].reshape(buckets, step)
    peaks = np.abs(trimmed).max(axis=1)
    top = float(peaks.max() or 1.0)
    return [round(float(p / top), 3) for p in peaks]


def slice_audio(audio, start: float, end: float, rate: int = 16000):
    """从 float32 数组里按秒取一段（说话人 embedding 用）。"""
    a, b = max(0, int(start * rate)), max(0, int(end * rate))
    return audio[a:b]


def rms(audio) -> float:
    import numpy as np

    if len(audio) == 0:
        return 0.0
    return float(np.sqrt((audio.astype("float64") ** 2).mean()))
