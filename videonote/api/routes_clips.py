# -*- coding: utf-8 -*-
"""跟读句库 + Anki 导出。

把一个句子「收藏」成 clip：自动切出这一句的音频（给 Anki 卡）和可选视频片段，
记复习次数，最后打成一个真正的 .apkg 牌组（带音频），导入 Anki 就能在手机上练。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..core.media import extract_audio_clip, extract_clip
from ..core.registry import register_router

router = APIRouter(tags=["clips"])


class ClipIn(BaseModel):
    seg_idx: int = -1
    seg_idxs: Optional[List[int]] = None
    tags: str = ""
    note: str = ""
    translation: str = ""
    with_video: bool = False
    force: bool = False


class ClipPatch(BaseModel):
    note: Optional[str] = None
    tags: Optional[str] = None
    translation: Optional[str] = None


# --------------------------------------------------------------------------
# 收藏 / 列表
# --------------------------------------------------------------------------
def _clip_dir(pid: str) -> Path:
    d = settings.project_dir(pid) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seg_map(pid: str) -> Dict[int, dict]:
    return {int(s["idx"]): s for s in db.list_segments(pid)}


def _make_media(pid: str, seg: dict, with_video: bool, force: bool = False) -> Dict[str, str]:
    """给一句话切音频（和可选视频）。已经切过就复用。"""
    project = db.get_project(pid) or {}
    src = project.get("media_path") or ""
    out: Dict[str, str] = {}
    if not src or not Path(src).exists():
        return out
    d = _clip_dir(pid)
    idx = int(seg["idx"])
    start, end = float(seg["start"]), float(seg["end"])
    # 前后各留 0.15s，避免首尾被切掉
    start, end = max(0.0, start - 0.15), end + 0.15

    audio = d / f"{pid}_{idx}.mp3"
    if not audio.exists() or force:
        if audio.exists():
            audio.unlink()
        audio = extract_audio_clip(src, start, end, audio)
    out["audio"] = str(audio)

    if with_video:
        video = d / f"{pid}_{idx}.mp4"
        if not video.exists() or force:
            try:
                video = extract_clip(src, start, end, video)
                out["video"] = str(video)
            except Exception:  # noqa: BLE001 - 纯音频项目切不出视频很正常
                pass
        else:
            out["video"] = str(video)
    return out


@router.post("/projects/{pid}/clips")
def create_clips(pid: str, body: ClipIn):
    """收藏句子进句库（支持批量）。会自动切片。"""
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    segs = _seg_map(pid)
    if not segs:
        raise HTTPException(400, "这个项目还没有转写结果")
    wanted = body.seg_idxs or ([body.seg_idx] if body.seg_idx >= 0 else [])
    wanted = [int(i) for i in wanted if int(i) in segs]
    if not wanted:
        raise HTTPException(400, "没有指定有效的句子序号")

    def work(i: int) -> dict:
        seg = segs[i]
        media = _make_media(pid, seg, body.with_video, body.force)
        return db.upsert_clip(
            pid, i,
            start=float(seg["start"]), end=float(seg["end"]), text=seg.get("text", ""),
            translation=body.translation, tags=body.tags, note=body.note,
            audio_path=media.get("audio", ""), video_path=media.get("video", ""),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        clips = list(pool.map(work, wanted))
    return {"clips": clips, "created": len(clips)}


@router.get("/projects/{pid}/clips")
def list_project_clips(pid: str, tag: str = ""):
    clips = db.list_clips(pid, tag=tag)
    return {"clips": clips, "scores": db.best_scores(pid)}


@router.get("/clips")
def list_all_clips(tag: str = ""):
    """跨项目看所有收藏句（做全局复习用）。"""
    clips = db.list_clips(None, tag=tag)
    titles = {p["id"]: p["title"] for p in db.list_projects()}
    for c in clips:
        c["project_title"] = titles.get(c["project_id"], c["project_id"])
    return {"clips": clips, "total": len(clips)}


@router.patch("/clips/{cid}")
def patch_clip(cid: int, body: ClipPatch):
    if not db.get_clip(cid):
        raise HTTPException(404, "句子不在句库里")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    db.update_clip(cid, **fields)
    return {"clip": db.get_clip(cid)}


@router.delete("/clips/{cid}")
def delete_clip(cid: int):
    clip = db.get_clip(cid)
    if not clip:
        raise HTTPException(404, "句子不在句库里")
    for key in ("audio_path", "video_path"):
        p = clip.get(key)
        if p and Path(p).exists():
            try:
                Path(p).unlink()
            except OSError:
                pass
    db.delete_clip(cid)
    return {"ok": True}


@router.post("/clips/{cid}/review")
def review_clip(cid: int):
    if not db.get_clip(cid):
        raise HTTPException(404, "句子不在句库里")
    db.touch_clip_review(cid)
    return {"clip": db.get_clip(cid)}


@router.get("/clips/{cid}/media")
def clip_media(cid: int, kind: str = "audio"):
    clip = db.get_clip(cid)
    if not clip:
        raise HTTPException(404, "句子不在句库里")
    p = clip.get("video_path" if kind == "video" else "audio_path") or ""
    if not p or not Path(p).exists():
        raise HTTPException(404, "这个句子还没有切片文件（重新收藏一次即可生成）")
    mt = "video/mp4" if kind == "video" else ("audio/wav" if p.endswith(".wav") else "audio/mpeg")
    return FileResponse(p, media_type=mt)


# --------------------------------------------------------------------------
# Anki 导出
# --------------------------------------------------------------------------
def _stable_id(text: str, base: int = 1_000_000_000) -> int:
    return base + (int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) % 900_000_000)


def _anki_tags(clip: dict, project_title: str) -> List[str]:
    raw = f"{clip.get('tags') or ''} VideoNote {project_title}"
    parts = re.split(r"[,，、;；/\s]+", raw)
    out = []
    for p in parts:
        p = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", p).strip("_")
        if p and p not in out:
            out.append(p)
    return out[:20] or ["VideoNote"]


def _build_apkg(pid: str, clips: List[dict], project_title: str, deck_name: str = "") -> Path:
    import genanki

    model = genanki.Model(
        _stable_id("videonote-model-v1", 1_600_000_000),
        "VideoNote 跟读卡",
        fields=[{"name": n} for n in ("Sentence", "Translation", "Audio", "Video", "Note", "Source")],
        templates=[{
            "name": "跟读",
            "qfmt": """<div class="vn-sentence">{{Sentence}}</div>
{{Audio}}
<div class="vn-hint">听完跟读一遍，再翻面看译文</div>""",
            "afmt": """{{FrontSide}}<hr id="answer">
<div class="vn-tr">{{Translation}}</div>
<div class="vn-note">{{Note}}</div>
{{Video}}
<div class="vn-src">{{Source}}</div>""",
        }],
        css="""
.card { font-family: -apple-system, "Microsoft YaHei", sans-serif; font-size: 20px;
        text-align: center; color: #111; background: #fafafa; padding: 18px; }
.vn-sentence { font-size: 24px; font-weight: 600; margin-bottom: 14px; line-height: 1.5; }
.vn-tr { color: #2b6cb0; margin: 12px 0; line-height: 1.6; }
.vn-note { color: #444; font-size: 16px; }
.vn-src { color: #999; font-size: 13px; margin-top: 14px; }
.vn-hint { color: #999; font-size: 14px; margin-top: 10px; }
""",
    )

    name = deck_name.strip() or f"VideoNote::{project_title}"
    deck = genanki.Deck(_stable_id("deck:" + name), name)
    media: List[str] = []

    for c in clips:
        audio = c.get("audio_path") or ""
        video = c.get("video_path") or ""
        media_files = []
        if audio and Path(audio).exists():
            media.append(audio)
            media_files.append(f"[sound:{Path(audio).name}]")
        if video and Path(video).exists():
            media.append(video)
            media_files.append(f"<video controls src='{Path(video).name}'></video>")
        note = genanki.Note(
            model=model,
            fields=[
                c.get("text", ""),
                c.get("translation", "") or "",
                " ".join(media_files[:1]),
                media_files[1] if len(media_files) > 1 else "",
                c.get("note", "") or "",
                f"{project_title} · {int(c.get('start', 0))//60:02d}:{int(c.get('start',0))%60:02d}",
            ],
            tags=_anki_tags(c, project_title),
        )
        deck.add_note(note)

    out_dir = settings.project_dir(pid) / "artifacts" / "anki"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_safe(project_title)}.apkg"
    pkg = genanki.Package(deck)
    pkg.media_files = media
    pkg.write_to_file(str(out))
    return out


def _build_zip(pid: str, clips: List[dict], project_title: str) -> Path:
    """没有 genanki 时的兜底：TSV + 音频文件打包，Anki 可导入 TSV。"""
    out_dir = settings.project_dir(pid) / "artifacts" / "anki"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_safe(project_title)}_tsv.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        rows = ["#separator:tab", "#html:true", "#columns:句子\t译文\t音频\t备注"]
        for c in clips:
            audio = Path(c.get("audio_path") or "")
            tag = f"[sound:{audio.name}]" if audio.exists() else ""
            rows.append("\t".join([c.get("text", ""), c.get("translation", "") or "", tag, c.get("note", "") or ""]))
            if audio.exists():
                z.write(audio, audio.name)
        z.writestr("cards.txt", "\n".join(rows))
        z.writestr("说明.txt", "用 Anki 的「文件 - 导入」选择 cards.txt，字段用制表符分隔，音频在同目录。")
    return out


@router.post("/projects/{pid}/clips/export")
def export_anki(pid: str, deck_name: str = "", with_video: bool = False, force: bool = False):
    """把句库导出成 Anki 牌组（.apkg，带音频；没装 genanki 就退化成 TSV 压缩包）。"""
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    clips = db.list_clips(pid)
    if not clips:
        raise HTTPException(400, "句库是空的，先收藏几个句子")

    # 补齐缺失的切片（老数据 / 之前没切成功的）
    segs = _seg_map(pid)
    for c in clips:
        need_audio = not (c.get("audio_path") and Path(c["audio_path"]).exists())
        need_video = with_video and not (c.get("video_path") and Path(c["video_path"]).exists())
        if (need_audio or need_video) and int(c["seg_idx"]) in segs:
            media = _make_media(pid, segs[int(c["seg_idx"])], with_video, force=force)
            db.update_clip(c["id"], audio_path=media.get("audio", c.get("audio_path") or ""),
                           video_path=media.get("video", c.get("video_path") or ""))
    clips = db.list_clips(pid)

    title = project.get("title") or "VideoNote"
    try:
        out = _build_apkg(pid, clips, title, deck_name)
        kind = "apkg"
    except ImportError:
        out = _build_zip(pid, clips, title)
        kind = "zip"

    db.put_artifact(pid, "anki_deck", str(out), kind=kind,
                    meta={"clips": len(clips), "deck": deck_name or f"VideoNote::{title}"})
    return {"path": str(out), "kind": kind, "clips": len(clips),
            "url": f"/api/projects/{pid}/artifact/anki_deck?download=true"}


def _safe(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name).strip()
    return (s[:60] or "videonote")


register_router(router)
