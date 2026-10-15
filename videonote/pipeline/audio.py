# -*- coding: utf-8 -*-
"""audio —— 抽 16k 单声道 wav，本地/云端 ASR 共用。"""
from __future__ import annotations

from typing import Any, Dict

from ..core.media import extract_audio
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext


@register_stage
class AudioStage(Stage):
    name = "audio"
    label = "抽取音频"
    description = "用 PyAV 解码成 16kHz 单声道 wav（Whisper 的标准输入）"
    requires = ("media_file",)
    produces = "audio"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        src = ctx.media_path
        if not src:
            raise RuntimeError("没有媒体文件")
        dst = ctx.dir / "audio" / "audio_16k.wav"
        extract_audio(src, dst, 16000, progress=ctx.set_progress)
        size_mb = dst.stat().st_size / 1024 / 1024
        ctx.put_artifact("audio", dst, kind="audio", meta={"size": dst.stat().st_size})
        ctx.set_progress(100, f"音频就绪 {size_mb:.1f} MB")
        return {"audio": str(dst), "size_mb": round(size_mb, 2)}
