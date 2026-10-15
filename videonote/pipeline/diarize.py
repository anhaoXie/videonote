# -*- coding: utf-8 -*-
"""diarize —— 说话人分离：给每句话标上是「谁」说的。

默认用本地 ECAPA 声纹 + 聚类（免费、离线、CPU 可跑）；
想更准可以换成 pyannote（需要 HF token，见 .env 的 VN_HF_TOKEN）。
"""
from __future__ import annotations

from typing import Any, Dict

from .. import db
from ..config import settings
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..providers.base import get_diarizer


@register_stage
class DiarizeStage(Stage):
    name = "diarize"
    label = "说话人分离"
    description = "分辨每句话是谁说的（本地声纹模型，CPU 可跑；美剧/访谈特别有用）"
    requires = ("audio", "transcript")
    produces = "diarization"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        audio = ctx.artifact_file("audio")
        if not audio:
            raise RuntimeError("找不到音频文件")
        segments = ctx.segments()
        if not segments:
            raise RuntimeError("没有转写结果，先跑语音识别")

        num = ctx.params.get("num_speakers")
        if num in (None, "", 0, "0"):
            num = settings.diarize_num_speakers or None
        else:
            num = int(num)
        provider = get_diarizer(ctx.params.get("diarize_provider"))
        ctx.log(f"使用说话人分离: {provider.describe()}（{'自动判断人数' if not num else str(num) + ' 人'}）")

        result = provider.diarize(str(audio), segments, num_speakers=num, progress=ctx.set_progress)
        assignments = result.get("assignments") or {}
        if not assignments:
            raise RuntimeError(result.get("note") or "没有识别出任何说话人")

        db.set_speakers(ctx.project_id, assignments)
        speakers = result.get("speakers") or []
        ctx.save_json("diarization", result, "diarization.json",
                      meta={"speakers": len(speakers), "engine": result.get("engine", "")})
        # 说话人结果变了，之前的拆句结论要重算：
        # 删掉产出，让 split_speakers 可以再跑一次（它对已拆过的句子是幂等的）。
        db.delete_artifact(ctx.project_id, "split_report")
        from ..core import events

        events.publish({"type": "diarized", "project_id": ctx.project_id, "speakers": len(speakers)})
        ctx.set_progress(100, f"识别出 {len(speakers)} 位说话人")
        return {"speakers": len(speakers), "assignments": len(assignments),
                "engine": result.get("engine", ""), "clusters": result.get("clusters")}
