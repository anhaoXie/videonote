# -*- coding: utf-8 -*-
"""系统接口：健康检查、能力清单（插件/阶段/任务类型/provider）、SSE 实时进度。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .. import __version__, db
from ..config import settings
from ..core import events
from ..core.jobs import worker
from ..core.registry import registry

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {"ok": True, "version": __version__, "worker_busy": worker.busy, "time": time.time()}


@router.get("/capabilities")
def capabilities():
    """前端靠这个自动渲染功能菜单 —— 加了新插件不用改前端。"""
    stages = []
    for name, cls in registry.stages.items():
        stages.append({
            "name": name,
            "label": getattr(cls, "label", name) or name,
            "description": getattr(cls, "description", ""),
            "requires": list(getattr(cls, "requires", ()) or ()),
            "produces": getattr(cls, "produces", ""),
            "optional": bool(getattr(cls, "optional", False)),
        })
    stages.sort(key=lambda s: s["name"])
    job_kinds = [
        {"name": k, "label": v.get("label", k), "description": v.get("description", ""), "stages": v.get("stages", [])}
        for k, v in registry.job_kinds.items()
    ]
    return {
        "version": __version__,
        "config": settings.redacted(),
        "phoneme": phoneme_status(),
        "stages": stages,
        "job_kinds": job_kinds,
        "providers": {k: sorted(v.keys()) for k, v in registry.providers.items()},
        "plugins": {
            "stages": sorted(registry.stages.keys()),
            "routers": [getattr(r, "prefix", str(r)) for r in registry.routers],
            "import_errors": getattr(registry, "import_errors", {}),
        },
    }


def phoneme_status() -> dict:
    """音素评测的可用性（模型没下完要说清楚，别让用户以为坏了）。"""
    try:
        from ..core import phonemes
        return phonemes.status()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"音素模块加载失败：{exc}"}


@router.get("/events")
async def sse(request: Request):
    """SSE 实时进度。前端用 EventSource 订阅，进度条就动起来了。"""
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            last_beat = time.time()
            while True:
                if await request.is_disconnected():
                    break
                payload: Dict[str, Any] | None = None
                try:
                    payload = q.get_nowait()
                except Exception:  # noqa: BLE001
                    payload = None
                if payload is None:
                    await asyncio.sleep(0.35)
                    if time.time() - last_beat > 15:
                        last_beat = time.time()
                        yield ": ping\n\n"
                    continue
                last_beat = time.time()
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.get("/stats")
def stats():
    projects = db.list_projects()
    return {
        "projects": len(projects),
        "jobs": len(db.list_jobs(limit=1000)),
        "data_dir": str(settings.data_dir),
    }
