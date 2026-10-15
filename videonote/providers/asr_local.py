"""本地语音识别：faster-whisper（CTranslate2, CPU int8）。

特点：免费、离线；CPU 上 small 模型大约 0.2~0.5x 实时（1 小时视频 2~5 小时？不，实测约 20~40 分钟）。
想更快就把 VN_WHISPER_MODEL 换成 base/tiny；想更准换 medium（更慢）。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from ..config import settings
from ..core.audio import read_wav_float32
from ..models import Segment, Transcript
from .base import ASRProvider
from ..core.registry import register_provider

_model_cache: dict = {}
_model_lock = threading.Lock()


def load_model(model_size: Optional[str] = None, device: Optional[str] = None, compute_type: Optional[str] = None):
    """加载并缓存 WhisperModel（第一次会下载模型，走 HF 镜像）。"""
    from faster_whisper import WhisperModel

    size = model_size or settings.whisper_model_dir or settings.whisper_model
    dev = device or settings.whisper_device
    ct = compute_type or settings.whisper_compute_type
    key = (str(size), dev, ct)
    with _model_lock:
        if key not in _model_cache:
            settings.models_dir.mkdir(parents=True, exist_ok=True)
            _model_cache[key] = WhisperModel(
                str(size),
                device=dev,
                compute_type=ct,
                download_root=str(settings.models_dir),
                num_workers=1,
            )
        return _model_cache[key]


class FasterWhisperASR(ASRProvider):
    name = "local"

    def describe(self) -> dict:
        return {
            "provider": "local",
            "engine": "faster-whisper",
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
        }

    def transcribe(self, audio_path: str | Path, language: Optional[str] = None,
                   progress: Optional[Callable[[float, str], None]] = None) -> Transcript:
        model = load_model()
        audio = read_wav_float32(audio_path)
        segments_iter, info = model.transcribe(
            audio,
            language=language or None,
            beam_size=settings.whisper_beam_size,
            vad_filter=settings.whisper_vad,
            vad_parameters={"min_silence_duration_ms": 500} if settings.whisper_vad else None,
            word_timestamps=True,
            condition_on_previous_text=False,   # 减少串味/复读
        )
        duration = float(getattr(info, "duration", 0) or 0)
        out = Transcript(language=getattr(info, "language", "") or "",
                         duration=duration, model=settings.whisper_model, provider="faster-whisper")
        for i, seg in enumerate(segments_iter):
            words = []
            for w in (seg.words or []):
                words.append({"w": w.word, "start": float(w.start or 0), "end": float(w.end or 0),
                              "prob": float(getattr(w, "probability", 0) or 0)})
            out.segments.append(Segment(idx=i, start=float(seg.start or 0), end=float(seg.end or 0),
                                        text=(seg.text or "").strip(), words=words))
            if progress and duration > 0:
                progress(min(99.0, float(seg.end or 0) / duration * 100.0),
                         f"识别中 {int(seg.end or 0)}s / {int(duration)}s")
        if progress:
            progress(100.0, f"识别完成，共 {len(out.segments)} 句")
        return out


@register_provider("asr", "local")
def _make() -> ASRProvider:
    return FasterWhisperASR()
