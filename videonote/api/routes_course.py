# -*- coding: utf-8 -*-
"""课程视频接口：文本/主题 -> 分集教学视频。"""
from __future__ import annotations

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
    """列出已生成的成片（按集号）。"""
    items = []
    if COURSE_ROOT.exists():
        for d in sorted(COURSE_ROOT.glob("ep*")):
            mp4 = d / "episode.mp4"
            if not mp4.exists():
                continue
            st = mp4.stat()
            items.append({
                "episode": d.name,
                "size_mb": round(st.st_size / 1048576, 2),
                "mtime": int(st.st_mtime),
            })
    return {"items": items}


@router.get("/course/video")
def course_video(ep: str):
    """播放/下载某一集的成片。"""
    if not EP_RE.match(ep or ""):
        raise HTTPException(400, "集号格式不对")
    mp4 = (COURSE_ROOT / ep / "episode.mp4").resolve()
    if not mp4.exists():
        raise HTTPException(404, "这一集还没生成")
    return FileResponse(str(mp4), media_type="video/mp4", filename=f"{ep}.mp4")
