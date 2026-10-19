# -*- coding: utf-8 -*-
"""subtitles —— 自动生成字幕（SRT / VTT），可选压制成硬字幕，可选双语（配合 translate 阶段）。

这就是你说的「导入一部美剧自动生成字幕」：跑一次 analyze 或 subtitles 任务，
字幕就出现在项目里，可直接下载 / 直接压片。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..core.zh import to_simplified


@register_stage
class SubtitlesStage(Stage):
    name = "subtitles"
    label = "生成字幕"
    description = "把逐句时间轴导出为 SRT/VTT（有译文时自动输出双语）"
    requires = ("transcript",)
    produces = "subtitles_srt"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        segs = ctx.segments()
        if not segs:
            raise RuntimeError("没有句子可供生成字幕")
        translation: Dict[str, str] = ctx.load_json("translation", {}) or {}
        bilingual = bool(translation) and ctx.params.get("bilingual", True)

        cues: List[Dict[str, Any]] = []
        for s in segs:
            text = (s.get("text") or "").strip()
            # 最后一道保险：译文落地成字幕前再转一次简体。
            # 老项目里已经存着繁体 translation.json，重跑字幕也会被这里纠正。
            zh = to_simplified((translation.get(str(s.get("idx"))) or "").strip())
            if bilingual and zh:
                text = f"{text}\n{zh}"
            cues.append({"start": float(s["start"]), "end": float(s["end"]), "text": text})

        srt = to_srt(cues)
        vtt = to_vtt(cues)
        srt_path = ctx.artifact_path("subtitles", "subtitle.srt")
        srt_path.write_text(srt, encoding="utf-8")
        vtt_path = ctx.artifact_path("subtitles", "subtitle.vtt")
        vtt_path.write_text(vtt, encoding="utf-8")

        from .. import db

        db.put_artifact(ctx.project_id, "subtitles_srt", str(srt_path), kind="subtitle",
                        meta={"cues": len(cues), "bilingual": bilingual})
        db.put_artifact(ctx.project_id, "subtitles_vtt", str(vtt_path), kind="subtitle",
                        meta={"cues": len(cues), "bilingual": bilingual})
        ctx.set_progress(100, f"字幕完成：{len(cues)} 条{'（双语）' if bilingual else ''}")
        return {"cues": len(cues), "srt": str(srt_path), "vtt": str(vtt_path), "bilingual": bilingual}


@register_stage
class BurnSubtitleStage(Stage):
    name = "burn"
    label = "压制硬字幕"
    description = "用 ffmpeg 把字幕烧进画面，导出一个可直接分发的 mp4（耗时较长）"
    requires = ("media_file", "subtitles_srt")
    produces = "video_hardsub"
    optional = False

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        from ..core.media import burn_subtitles

        srt = ctx.artifact_file("subtitles_srt")
        src = ctx.media_path
        if not srt or not src:
            raise RuntimeError("缺少媒体或字幕文件")
        out = ctx.artifact_path("video_hardsub", "with_subtitles.mp4")
        ctx.set_progress(5, "开始压制（视频越长越慢，可以先去干别的）")
        burn_subtitles(src, srt, out, font_size=int(ctx.params.get("font_size", 22)))
        db_meta = {"size": out.stat().st_size}
        from .. import db

        db.put_artifact(ctx.project_id, "video_hardsub", str(out), kind="video", meta=db_meta)
        ctx.set_progress(100, "压制完成")
        return {"video": str(out), **db_meta}


# --------------------------------------------------------------------------
def _fmt(seconds: float, sep: str = ",") -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, limit: int = 42, max_lines: int = 2) -> str:
    """字幕换行：按目标行宽切分，**绝不丢字**（宁可 3 行也不能吃掉句子尾巴）。

    中英混排都能处理：英文按词切，中文按字切。
    """
    import math
    import re

    token_re = re.compile(r"[A-Za-z0-9'’\-]+|\s+|.", re.S)
    lines: List[str] = []
    for raw in str(text).split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        # 目标每行长度：让总行数尽量等于 max_lines，但不超过 limit、不小于 12
        per = int(min(limit, max(12, math.ceil(len(raw) / max(1, max_lines)))))
        cur = ""
        for tok in token_re.findall(raw):
            if tok.isspace():
                if cur:
                    cur += " " if not cur.endswith(" ") else ""
                continue
            if cur and len(cur.rstrip()) + len(tok) > per:
                lines.append(cur.rstrip())
                cur = ""
            cur += tok
        if cur.strip():
            lines.append(cur.rstrip())
    return "\n".join(lines)


def to_srt(cues: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    for i, c in enumerate(cues, start=1):
        end = max(float(c["end"]), float(c["start"]) + 0.4)
        out.append(str(i))
        out.append(f"{_fmt(c['start'])} --> {_fmt(end)}")
        out.append(_wrap(str(c.get("text", ""))))
        out.append("")
    return "\n".join(out)


def to_vtt(cues: List[Dict[str, Any]]) -> str:
    out: List[str] = ["WEBVTT", ""]
    for c in cues:
        end = max(float(c["end"]), float(c["start"]) + 0.4)
        out.append(f"{_fmt(c['start'], '.')} --> {_fmt(end, '.')}")
        out.append(_wrap(str(c.get("text", ""))))
        out.append("")
    return "\n".join(out)
