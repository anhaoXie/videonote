"""事件总线：后端管线进度 -> 前端 SSE 实时推送。

用途：转写跑到第几分钟、当前在处理哪个阶段，前端进度条实时动。
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Dict, List, Optional

_global_queue: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=10000)
_subscribers: List["queue.Queue[Optional[dict]]"] = []
_lock = threading.Lock()


def publish(event: Dict[str, Any]) -> None:
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass
    try:
        _global_queue.put_nowait(event)
    except queue.Full:
        pass


def subscribe() -> "queue.Queue[Optional[dict]]":
    q: "queue.Queue[Optional[dict]]" = queue.Queue(maxsize=1000)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: "queue.Queue[Optional[dict]]") -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def progress(job_id: Optional[str], project_id: Optional[str], stage: str, pct: float,
             message: str = "", extra: Optional[dict] = None) -> None:
    publish({
        "type": "progress",
        "job_id": job_id,
        "project_id": project_id,
        "stage": stage,
        "pct": round(float(pct), 2),
        "message": message,
        "extra": extra or {},
    })


def log_line(job_id: Optional[str], project_id: Optional[str], message: str, level: str = "info") -> None:
    publish({"type": "log", "job_id": job_id, "project_id": project_id, "level": level, "message": message})


def notify(job_id: Optional[str], project_id: Optional[str], status: str, message: str = "") -> None:
    publish({"type": "job", "job_id": job_id, "project_id": project_id, "status": status, "message": message})
