"""云端语音识别：任何 OpenAI /audio/transcriptions 兼容接口。

实测可用的：硅基流动（SenseVoiceSmall / whisper-large-v3）、OpenAI(whisper-1)、Groq、本地部署的 whisper.cpp server 等。
把 VN_ASR_PROVIDER 改成 cloud 即可启用。文件大时自动用 verbose_json 拿分段。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from ..config import settings
from ..models import Segment, Transcript
from .base import ASRProvider
from ..core.registry import register_provider


class OpenAICompatASR(ASRProvider):
    name = "cloud"

    def describe(self) -> dict:
        return {"provider": "cloud", "base_url": settings.asr_base_url, "model": settings.asr_model,
                "key_set": bool(settings.asr_api_key)}

    def transcribe(self, audio_path: str | Path, language: Optional[str] = None,
                   progress: Optional[Callable[[float, str], None]] = None) -> Transcript:
        if not settings.asr_api_key:
            raise RuntimeError("云端 ASR 未配置 API key（VN_ASR_API_KEY）")
        url = settings.asr_base_url.rstrip("/") + "/audio/transcriptions"
        p = Path(audio_path)
        size_mb = p.stat().st_size / 1024 / 1024
        if progress:
            progress(10, f"上传音频 {size_mb:.1f} MB 到 {settings.asr_base_url}")
        data: dict[str, Any] = {"model": settings.asr_model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        headers = {"Authorization": f"Bearer {settings.asr_api_key}"}
        with httpx.Client(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            with p.open("rb") as fh:
                resp = client.post(url, headers=headers, data=data, files={"file": (p.name, fh, "audio/wav")})
        if resp.status_code >= 400:
            raise RuntimeError(f"云端 ASR 返回 {resp.status_code}: {resp.text[:400]}")
        payload = resp.json()
        out = Transcript(language=payload.get("language", "") or "", duration=float(payload.get("duration", 0) or 0),
                         model=settings.asr_model, provider="cloud")
        for i, s in enumerate(payload.get("segments") or []):
            out.segments.append(Segment(idx=i, start=float(s.get("start", 0) or 0),
                                        end=float(s.get("end", 0) or 0),
                                        text=(s.get("text") or "").strip()))
        if not out.segments and payload.get("text"):
            out.segments.append(Segment(idx=0, start=0.0, end=out.duration or 0.0, text=payload["text"].strip()))
        if progress:
            progress(100, f"识别完成，共 {len(out.segments)} 句")
        return out


@register_provider("asr", "cloud")
def _make() -> ASRProvider:
    return OpenAICompatASR()
