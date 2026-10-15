# -*- coding: utf-8 -*-
"""用 espeak-ng 合成语音（离线、零成本、不需要任何 API）。

为什么需要它：单词级的「最小对立对」训练（ship/sheep、think/sink）必须有音频 ——
但用户视频里不一定出现过这些词，TTS 又是唯一稳定的来源。
espeak-ng 的音质是机器音，但对分辨 /ɪ/ 和 /iː/ 这类**音位对立**完全够用：
它本来就是按音素规则合成的，两个词的差异清清楚楚。

实现要点：
  * 复用 core.phonemes 里已经加载好的那一个 espeak 实例（共享同一份 DLL 和数据目录，
    顺便绕开了「路径里有中文，espeak 用 ANSI 打不开」的坑）。
  * 合成走 espeak_SetSynthCallback 回调：样本一块块送过来，攒起来写成 WAV。
  * 结果按 (文本+语速+口音) 哈希缓存到 data/tts/，同一个词只合成一次。
"""
from __future__ import annotations

import hashlib
import threading
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np

from ..config import settings
from . import phonemes

_lock = threading.Lock()
_state: dict = {"cb": None, "buf": None, "rate": 0}

# 覆盖常见词 + 最小对立对，语速默认 150（正常偏慢，方便跟读）
DEFAULT_WPM = 150


def available() -> bool:
    try:
        phonemes._init_espeak()
        return True
    except Exception:  # noqa: BLE001
        return False


def _cache_dir() -> Path:
    d = settings.data_dir / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(text: str, wpm: int = DEFAULT_WPM, voice: str = "en-us") -> Path:
    key = f"{voice}|{wpm}|{text.strip().lower()}".encode("utf-8")
    return _cache_dir() / (hashlib.sha1(key).hexdigest()[:16] + ".wav")


def _install_callback(lib) -> None:
    """装一次回调：每次合成时把样本写进 _state['buf']。"""
    import ctypes

    if _state["cb"] is not None:
        return

    def _cb(wav_ptr, numsamples, events):        # noqa: ANN001
        if numsamples > 0 and wav_ptr:
            arr = ctypes.cast(wav_ptr, ctypes.POINTER(ctypes.c_short * numsamples)).contents
            _state["buf"].extend(arr)
        return 0

    cb = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short),
                          ctypes.c_int, ctypes.c_void_p)(_cb)
    lib.espeak_SetSynthCallback.argtypes = [ctypes.c_void_p]
    lib.espeak_SetSynthCallback(cb)
    _state["cb"] = cb                            # 必须留引用，否则会被 GC 回收导致崩溃

    lib.espeak_Synth.restype = ctypes.c_int
    lib.espeak_Synth.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint,
                                 ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                 ctypes.POINTER(ctypes.c_uint), ctypes.c_void_p]
    lib.espeak_Synchronize.restype = ctypes.c_int
    lib.espeak_SetParameter.restype = ctypes.c_int
    lib.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]


def synth(text: str, wpm: int = DEFAULT_WPM, voice: str = "en-us") -> Optional[np.ndarray]:
    """合成一段语音，返回 float32 的样本（采样率见 rate()）。失败返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    path = cache_path(text, wpm, voice)
    if path.exists():
        return _read_wav(path)
    try:
        import ctypes

        lib = phonemes._init_espeak()
        with _lock:
            _install_callback(lib)
            rate = phonemes._state.get("synth_rate") or _state["rate"]
            if not rate:
                rate = int(phonemes._espeak_ipa_raw("a") and 0) or 22050
            lib.espeak_SetVoiceByName(voice.encode("ascii"))
            # espeakRATE = 1
            lib.espeak_SetParameter(1, int(wpm), 0)
            _state["buf"] = []
            data = text.encode("utf-8")
            ok = lib.espeak_Synth(data, len(data), 0, 0, 0, 0x01, None, None)
            lib.espeak_Synchronize()
            samples = np.array(_state["buf"], dtype=np.int16)
            _state["buf"] = []
        if ok != 0 or samples.size == 0:
            return None
        _write_wav(path, samples, _sample_rate())
        return samples.astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001 - 合成失败不该让功能崩掉，界面退化到只看文本
        return None


def resample(x: np.ndarray, sr_in: int, sr_out: int = 16000) -> np.ndarray:
    """带限重采样（FFT）：先把高于新奈奎斯特频率的分量砍掉，再变换到新长度。

    为什么不能用 np.interp 那种线性插值：espeak 输出 22.05kHz，直接抽到 16kHz 会把
    8kHz 以上的能量**折叠回语音频段**（混叠），辅音被糊掉 —— 实测音素模型直接听不懂
    （ship 被听成 [ʃ iː]、zoo 听成空）。带限重采样后模型才认得出。
    """
    if x is None or len(x) == 0 or sr_in == sr_out:
        return x
    n_in = len(x)
    n_out = max(1, int(round(n_in * sr_out / sr_in)))
    spec = np.fft.rfft(x)
    keep = int(np.floor(min(n_in, n_out) / 2)) + 1
    out_spec = np.zeros(n_out // 2 + 1, dtype=complex)
    m = min(keep, len(spec), len(out_spec))
    out_spec[:m] = spec[:m]
    y = np.fft.irfft(out_spec, n=n_out)
    return (y * (n_out / n_in)).astype(np.float32)


def synth16(text: str, wpm: int = DEFAULT_WPM, voice: str = "en-us") -> Optional[np.ndarray]:
    """合成并重采样到 16kHz（音素模型要的采样率）。"""
    a = synth(text, wpm=wpm, voice=voice)
    if a is None:
        return None
    return resample(a, _sample_rate(), 16000)


def _sample_rate() -> int:
    """espeak-ng 默认 22050Hz；初始化时会返回，这里做一次兜底探测。"""
    if _state["rate"]:
        return _state["rate"]
    try:
        import ctypes

        lib = phonemes._init_espeak()
        lib.espeak_GetCurrentVoice.restype = ctypes.c_void_p
        # 用 GetSampleRate 之类的接口不一定存在，直接用约定值
    except Exception:  # noqa: BLE001
        pass
    _state["rate"] = 22050
    return _state["rate"]


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.astype(np.int16).tobytes())
    tmp.replace(path)


def _read_wav(path: Path) -> Optional[np.ndarray]:
    try:
        with wave.open(str(path), "rb") as f:
            data = f.readframes(f.getnframes())
        return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001
        return None


def ensure_word(word: str, wpm: int = DEFAULT_WPM) -> Optional[Path]:
    """保证某个词的音频存在，返回文件路径（给前端 <audio> 用）。"""
    p = cache_path(word, wpm)
    if p.exists():
        return p
    synth(word, wpm)
    return p if p.exists() else None


def wave_samples(path: Path) -> List[float]:
    a = _read_wav(path)
    return [] if a is None else a.tolist()
