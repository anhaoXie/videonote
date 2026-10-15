# -*- coding: utf-8 -*-
"""任务接口：查询、日志、取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db

router = APIRouter(tags=["jobs"])


@router.get("/jobs")
def list_jobs(project_id: str | None = None, limit: int = 50):
    return {"jobs": db.list_jobs(project_id, limit=limit)}


@router.get("/jobs/{jid}")
def get_job(jid: str, logs: bool = True):
    job = db.get_job(jid)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {"job": job, "logs": db.list_logs(jid) if logs else []}


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str):
    if not db.get_job(jid):
        raise HTTPException(404, "任务不存在")
    ok = db.cancel_job(jid)
    return {"ok": ok}
