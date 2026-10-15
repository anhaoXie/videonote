# -*- coding: utf-8 -*-
"""项目相关接口：新建/导入/列表/详情/删除 + 任务发起。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..core.jobs import worker
from ..core.registry import registry

router = APIRouter(tags=["projects"])

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".ts", ".m4v", ".mpg", ".mpeg",
             ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class CreateProjectIn(BaseModel):
    title: str = ""
    source_type: str = "url"          # url | file | empty
    url: str = ""
    path: str = ""                    # 服务器本地路径（本机文件直接引用）
    auto_start: bool = True
    job_kind: str = "analyze"
    params: Optional[dict] = None


class JobIn(BaseModel):
    kind: str = "analyze"
    params: Optional[dict] = None


@router.get("/projects")
def list_projects():
    projects = db.list_projects()
    for p in projects:
        jobs = db.list_jobs(p["id"], limit=1)
        p["last_job"] = jobs[0] if jobs else None
        p["artifact_keys"] = [a["key"] for a in db.list_artifacts(p["id"])]
    return {"projects": projects}


@router.post("/projects")
def create_project(body: CreateProjectIn):
    if body.source_type == "url":
        if not body.url.strip():
            raise HTTPException(400, "URL 不能为空")
        title = body.title.strip() or _title_from_url(body.url)
        project = db.create_project(title, "url", body.url.strip())
        job = _enqueue(project["id"], body.job_kind, {"source_type": "url", "source": body.url.strip(),
                                                      **(body.params or {})}) if body.auto_start else None
        return {"project": project, "job": job}

    if body.source_type == "file":
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(400, f"文件不存在: {body.path}")
        project = db.create_project(body.title.strip() or p.stem, "file", str(p))
        job = _enqueue(project["id"], body.job_kind, {"source_type": "file", "source": str(p),
                                                      **(body.params or {})}) if body.auto_start else None
        return {"project": project, "job": job}

    project = db.create_project(body.title.strip() or "未命名项目", "empty", "")
    return {"project": project, "job": None}


@router.post("/projects/upload")
async def upload_project(
    file: UploadFile = File(...),
    title: str = Form(""),
    auto_start: bool = Form(True),
    job_kind: str = Form("analyze"),
):
    """上传本地视频文件（大文件走流式写入，前端可显示上传进度）。"""
    name = file.filename or "upload.bin"
    suffix = Path(name).suffix.lower()
    if suffix and suffix not in VIDEO_EXT:
        # 不阻止，只提示；ffmpeg/PyAV 能解就行
        pass
    pid = "p_" + uuid.uuid4().hex[:12]
    media_dir = settings.project_dir(pid) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(name)
    dst = media_dir / safe
    size = 0
    with dst.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024 * 4)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
    if size == 0:
        shutil.rmtree(settings.project_dir(pid), ignore_errors=True)
        raise HTTPException(400, "上传的文件是空的")

    project = db.create_project(title.strip() or Path(safe).stem, "file", str(dst), pid=pid)
    db.put_artifact(project["id"], "upload_tmp", str(dst), kind="media", meta={"size": size})
    job = _enqueue(project["id"], job_kind, {"source_type": "file", "source": str(dst)}) if auto_start else None
    return {"project": db.get_project(project["id"]), "job": job, "size": size}


@router.get("/projects/{pid}")
def get_project(pid: str):
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    return {
        "project": project,
        "artifacts": db.list_artifacts(pid),
        "jobs": db.list_jobs(pid, limit=20),
        "segment_count": len(db.list_segments(pid)),
    }


@router.patch("/projects/{pid}")
def patch_project(pid: str, title: str = Form(...)):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    db.update_project(pid, title=title)
    return {"project": db.get_project(pid)}


@router.delete("/projects/{pid}")
def delete_project(pid: str, delete_files: bool = True):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    db.delete_project(pid)
    if delete_files:
        shutil.rmtree(settings.project_dir(pid), ignore_errors=True)
    return {"ok": True}


class RunIn(BaseModel):
    kind: str = "analyze"
    params: Optional[dict] = None


@router.post("/projects/{pid}/jobs")
def run_job(pid: str, body: RunIn):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    if body.kind not in registry.job_kinds:
        raise HTTPException(400, f"未知任务类型 {body.kind}（可用：{list(registry.job_kinds)}）")
    job = _enqueue(pid, body.kind, {"source_type": "file",
                                    "source": (db.get_project(pid) or {}).get("source_uri", ""),
                                    **(body.params or {})})
    return {"job": job}


# --------------------------------------------------------------------------
def _enqueue(pid: str, kind: str, params: dict) -> dict:
    job = db.create_job(pid, kind, params)
    db.update_project(pid, status="queued")
    worker.start()
    return job


def _title_from_url(url: str) -> str:
    tail = url.rstrip("/").split("/")[-1]
    return tail[:80] or "网络视频"


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*\n\r\t'
    out = "".join("_" if c in bad else c for c in name).strip()
    return out[:120] or "upload.bin"
