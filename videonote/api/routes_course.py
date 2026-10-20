# -*- coding: utf-8 -*-
"""课程视频接口：文本/主题 -> 分集教学视频。

产出布局：data/courses/<课程id>/course.json + epNN/episode.mp4
每次生成一个独立课程目录，避免按集号互相覆盖。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..core import course_jobs

router = APIRouter(tags=["course"])

COURSE_ROOT = Path("data/courses")
EP_RE = re.compile(r"^ep\d{2,3}$")
COURSE_RE = re.compile(r"^c[0-9a-zA-Z\-_]{4,40}$")


class CourseIn(BaseModel):
    source: str
    episodes: int = 3
    minutes: int = 3
    wpm: int = 150
    all: bool = True
    only: Optional[int] = 1


@router.post("/course/build")
def build_course(body: CourseIn):
    """排队一个课程生成任务（复用 jobs 表，project_id 为空）。"""
    if not (body.source or "").strip():
        raise HTTPException(400, "请提供主题或文本")
    job = db.create_job(None, course_jobs.KIND, params=body.model_dump())
    return {"job_id": job["id"]}


@router.get("/course/list")
def list_courses():
    """列出所有课程及其成片（按 course.json 清单，新的在前）。"""
    courses = []
    if not COURSE_ROOT.exists():
        return {"courses": courses}

    for d in sorted(COURSE_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        man = d / "course.json"
        if not man.exists():
            continue
        try:
            meta = json.loads(man.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue

        eps = []
        for e in meta.get("episodes") or []:
            rel = str(e.get("file") or "")
            if not rel:
                rel = f"ep{int(e.get('no') or 0):02d}/episode.mp4"
            mp4 = d / rel
            if not mp4.exists():
                continue
            eps.append({
                "no": e.get("no"),
                "title": e.get("title", ""),
                "episode": mp4.parent.name,
                "size_mb": round(mp4.stat().st_size / 1048576, 2),
                "seconds": e.get("seconds"),
            })

        courses.append({
            "id": d.name,
            "title": meta.get("title") or d.name,
            "created_at": meta.get("created_at", ""),
            "count": len(eps),
            "episodes": eps,
        })
    return {"courses": courses}


@router.get("/course/video")
def course_video(course: str, ep: str):
    """播放/下载某一集成片。"""
    if not COURSE_RE.match(course or "") or not EP_RE.match(ep or ""):
        raise HTTPException(400, "参数格式不对")
    base = COURSE_ROOT.resolve()
    mp4 = (base / course / ep / "episode.mp4").resolve()
    # 解析后必须仍在课程根目录之下（防 ../ 穿越）
    if base not in mp4.parents:
        raise HTTPException(400, "非法路径")
    if not mp4.exists():
        raise HTTPException(404, "这一集还没生成")
    return FileResponse(str(mp4), media_type="video/mp4", filename=f"{course}-{ep}.mp4")
