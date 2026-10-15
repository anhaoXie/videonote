# -*- coding: utf-8 -*-
"""split_speakers —— 按说话人拆句。

把「A 说一句 + B 回一句」被 `asr._tidy()` 合成同一个 segment 的情况切开，
每段标上真正的说话人。

**为什么是独立任务、不挂在 diarize 后面自动跑**：
拆句会重排句号（seg_idx），句库 / 评分 / 复习队列 / 译文都要跟着迁移。
这是「用户该知道自己在做什么」的操作，所以做成下拉框里单独可选的一项。
迁移前会自动备份数据库（`data/backups/`）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..core import split
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext


@register_stage
class SplitSpeakersStage(Stage):
    name = "split_speakers"
    label = "按说话人拆句"
    description = ("把被合成一句的多人对话按说话人切开（会重排句号；"
                   "句库/评分/复习队列/译文自动迁移，数据库先备份）")
    requires = ("transcript", "diarization")
    produces = "split_report"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        diar = ctx.load_json("diarization", {}) or {}
        windows: List[dict] = [w for w in (diar.get("windows") or []) if isinstance(w, dict)]
        if not windows:
            raise RuntimeError(
                "这份说话人分离结果里没有窗口级时间线（windows），没法拆句。"
                "请用 local_ecapa 重跑一次「说话人分离」。"
            )
        segments = ctx.segments()
        if not segments:
            raise RuntimeError("没有句子可拆（先跑语音识别）")

        new_segments, mapping = split.plan(segments, windows)
        n_split = split.changed_count(mapping)
        ctx.set_progress(25, f"分析完成：{len(segments)} 句里有 {n_split} 句需要拆")

        if not n_split:
            payload = {"changed": 0, "before": len(segments), "after": len(new_segments),
                       "note": "没有发现「一句话里有多个人」的情况，句子没有改动"}
            ctx.save_json("split_report", payload, "split_report.json",
                          meta={"changed": 0, "segments": len(segments)})
            ctx.set_progress(100, "没有需要拆的句子")
            return payload

        moved = split.migrate(ctx.project_id, new_segments, mapping)
        ctx.set_progress(80, "句子已改号，正在同步说话人时间线")
        split.remap_diarization(ctx.project_id, new_segments)

        speakers = sorted({str(s.get("speaker") or "") for s in new_segments if s.get("speaker")})
        payload = {
            "changed": n_split,
            "before": len(segments),
            "after": len(new_segments),
            "speakers": speakers,
            "moved": moved,
        }
        ctx.save_json("split_report", payload, "split_report.json",
                      meta={"changed": n_split, "before": len(segments), "after": len(new_segments)})

        from ..core import events

        events.publish({"type": "segments_changed", "project_id": ctx.project_id,
                        "before": len(segments), "after": len(new_segments)})
        ctx.set_progress(100,
                         f"拆开 {n_split} 句：{len(segments)} → {len(new_segments)} 句；"
                         f"已迁移 句库 {moved.get('clips', 0)} / 评分 {moved.get('scores', 0)} / "
                         f"复习 {moved.get('reviews', 0)}")
        return payload
