# -*- coding: utf-8 -*-
"""复习队列 + 训练统计接口。

* 复习队列：读错的句子自动排队，SM-2 调度，今天该练什么一目了然。
* 训练统计：练了多少、有没有变好、最该补哪个词。
* 顺带提供「单句音频」接口 —— 转写页和复习页都需要「只听这一句」。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..core import analytics, minimal_pairs as MP, srs
from ..core.media import extract_audio_clip
from ..core.registry import register_router

router = APIRouter(tags=["training"])


class SyncIn(BaseModel):
    project_id: str = ""


class GradeIn(BaseModel):
    score: float
    seconds: float = 0.0
    kind: str = "review"


class SnoozeIn(BaseModel):
    days: float = 1.0


class SuspendIn(BaseModel):
    suspended: bool = True


class AddIn(BaseModel):
    seg_idx: int
    reason: str = "手动加入复习"


# --------------------------------------------------------------------------
# 复习队列
# --------------------------------------------------------------------------
@router.get("/projects/{pid}/reviews")
def project_reviews(pid: str, limit: int = srs.DEFAULT_LIMIT, include_future: bool = False,
                    include_words: bool = True, word_ratio: float = srs.WORD_RATIO):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return {
        "queue": srs.unified_queue(pid=pid, limit=limit, include_future=include_future,
                                   include_words=include_words, word_ratio=word_ratio),
        "counts": srs.unified_counts(pid),
        "forecast": srs.forecast(pid, days=14),
    }


@router.get("/reviews/due")
def due_everywhere(limit: int = srs.DEFAULT_LIMIT, include_words: bool = True,
                   word_ratio: float = srs.WORD_RATIO):
    """跨项目的今日队列（首页/复习页用）。句子卡 + 单词卡混排。

    单词卡不绑项目（练的是你自己的发音），所以两个视图里都给。
    """
    return {
        "queue": srs.unified_queue(pid=None, limit=limit, include_words=include_words,
                                   word_ratio=word_ratio),
        "counts": srs.unified_counts(None),
    }


@router.get("/reviews")
def list_all(project_id: str = "", status: str = "", limit: int = 500):
    rows = db.list_reviews(pid=project_id or None, status=status, limit=limit)
    return {"reviews": srs.enrich(rows), "counts": srs.counts(project_id or None)}


@router.post("/reviews/sync")
def sync_reviews(body: SyncIn):
    """把历史评分/收藏补进队列（幂等，随时可调）。"""
    return srs.sync(body.project_id or None)


@router.post("/projects/{pid}/reviews")
def add_review(pid: str, body: AddIn):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return srs.add_manual(pid, body.seg_idx, body.reason)


@router.post("/reviews/{rid}/grade")
def grade_review(rid: int, body: GradeIn):
    try:
        return srs.grade(rid, body.score, seconds=body.seconds, kind=body.kind)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/reviews/{rid}/snooze")
def snooze_review(rid: int, body: SnoozeIn):
    try:
        return srs.snooze(rid, body.days)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/reviews/{rid}/suspend")
def suspend_review(rid: int, body: SuspendIn):
    if not db.get_review(rid):
        raise HTTPException(404, "复习项不存在")
    return srs.suspend(rid, body.suspended)


@router.post("/reviews/{rid}/reset")
def reset_review(rid: int):
    try:
        return srs.reset(rid)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/reviews/{rid}")
def delete_review(rid: int):
    db.delete_review(rid)
    return {"ok": True}


# --------------------------------------------------------------------------
# 单词卡：跟句子共用一套 SM-2，理由与设计见 core/srs.py 的 unified_queue
# --------------------------------------------------------------------------
class WordGradeIn(BaseModel):
    score: float
    seconds: float = 0.0
    kind: str = "review"


class WordAddIn(BaseModel):
    pair_id: str
    word: str
    reason: str = "手动加入复习"


@router.post("/reviews/words/sync")
def sync_word_reviews():
    """把历史练砸过的词补进队列（幂等，随时可调）。"""
    return srs.word_sync()


@router.get("/reviews/words")
def list_word_reviews(status: str = "", limit: int = 500):
    rows = db.list_word_reviews(status=status, limit=limit)
    return {"reviews": srs.word_enrich(rows), "counts": srs.word_counts()}


@router.post("/reviews/words")
def add_word_review(body: WordAddIn):
    if not MP.find(body.pair_id):
        raise HTTPException(404, f"没有这个词对：{body.pair_id}")
    return srs.word_add(body.pair_id, body.word, body.reason)


@router.post("/reviews/words/{wrid}/grade")
def grade_word_review(wrid: int, body: WordGradeIn):
    try:
        return srs.word_grade(wrid, body.score, seconds=body.seconds, kind=body.kind)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/reviews/words/{wrid}/snooze")
def snooze_word_review(wrid: int, body: SnoozeIn):
    try:
        return srs.word_snooze(wrid, body.days)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/reviews/words/{wrid}/suspend")
def suspend_word_review(wrid: int, body: SuspendIn):
    if not db.get_word_review(wrid):
        raise HTTPException(404, "单词卡不存在")
    return srs.word_suspend(wrid, body.suspended)


@router.delete("/reviews/words/{wrid}")
def delete_word_review(wrid: int):
    db.delete_word_review(wrid)
    return {"ok": True}


# --------------------------------------------------------------------------
# 训练统计
# --------------------------------------------------------------------------
@router.get("/analytics")
def analytics_report(project_id: str = "", days: int = 120):
    if project_id and not db.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return analytics.report(project_id or None, days=max(7, min(365, days)))


# --------------------------------------------------------------------------
# 单句音频（按需切片并缓存）—— 「只听这一句」
# --------------------------------------------------------------------------
@router.get("/projects/{pid}/segments/{idx}/audio")
def segment_audio(pid: str, idx: int):
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    seg = next((s for s in db.list_segments(pid) if int(s["idx"]) == int(idx)), None)
    if not seg:
        raise HTTPException(404, f"找不到第 {idx} 句")
    src = project.get("media_path") or ""
    if not src or not Path(src).exists():
        raise HTTPException(400, "原始媒体文件不在了，无法切片")

    d = settings.project_dir(pid) / "seg_audio"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"seg_{int(idx)}.mp3"
    if not dst.exists():
        start = max(0.0, float(seg["start"]) - 0.15)
        end = float(seg["end"]) + 0.15
        try:
            dst = extract_audio_clip(src, start, end, dst)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"切片失败：{exc}") from exc
    return FileResponse(dst, media_type="audio/mpeg")


register_router(router)
