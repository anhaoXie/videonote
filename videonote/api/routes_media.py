# -*- coding: utf-8 -*-
"""媒体与产出接口：视频流（支持拖动进度条）、转写、句子、笔记、关键帧、字幕下载。"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from .. import db

router = APIRouter(tags=["media"])

CHUNK = 1024 * 512
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _resolve(pid: str, key: Optional[str] = None) -> Path:
    project = db.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    if key:
        art = db.get_artifact(pid, key)
        if not art or not art.get("path"):
            raise HTTPException(404, f"没有产出: {key}")
        path = Path(art["path"])
    else:
        path = Path(project.get("media_path") or "")
    if not path.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    return path


def _stream(path: Path, request: Request, as_attachment: bool = False) -> Response:
    """自己实现 Range，保证 <video> 能拖动、能秒开。"""
    size = path.stat().st_size
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() in (".srt", ".vtt"):
        ctype = "text/plain; charset=utf-8"
    disposition = f'attachment; filename="{path.name}"' if as_attachment else "inline"
    range_header = request.headers.get("range") or request.headers.get("Range")
    start, end = 0, size - 1
    status = 200
    if range_header:
        m = RANGE_RE.match(range_header.strip())
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": disposition,
        "Cache-Control": "no-cache",
    }

    def gen():
        remaining = end - start + 1
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(gen(), status_code=status, media_type=ctype, headers=headers)


@router.get("/projects/{pid}/raw")
def media_stream(pid: str, request: Request):
    return _stream(_resolve(pid), request)


@router.get("/projects/{pid}/artifact/{key}")
def artifact_stream(pid: str, key: str, request: Request, download: bool = False):
    return _stream(_resolve(pid, key), request, as_attachment=download)


@router.get("/projects/{pid}/transcript")
def get_transcript(pid: str):
    data = _load_json_artifact(pid, "transcript")
    if not data:
        raise HTTPException(404, "还没有转写结果")
    return data


@router.get("/projects/{pid}/segments")
def get_segments(pid: str, q: str = "", start: float | None = None, end: float | None = None,
                 offset: int = 0, limit: int = 1000):
    """句子查询 —— 播放器字幕同步、跟读模式、句子搜索都用它。"""
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    segs = db.list_segments(pid)
    if start is not None:
        segs = [s for s in segs if s["end"] >= start]
    if end is not None:
        segs = [s for s in segs if s["start"] <= end]
    if q:
        kw = q.lower()
        segs = [s for s in segs if kw in (s["text"] or "").lower()]
    total = len(segs)
    return {"total": total, "segments": segs[offset : offset + limit]}


@router.get("/projects/{pid}/notes")
def get_notes(pid: str):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    md = ""
    p = _path_of(pid, "notes")
    if p and p.exists():
        md = p.read_text(encoding="utf-8")
    return {"markdown": md, "summary": _load_json_artifact(pid, "summary") or {}}


@router.get("/projects/{pid}/frames")
def get_frames(pid: str, limit: int = 400):
    frames = _load_json_artifact(pid, "frames") or []
    return {"total": len(frames), "frames": frames[:limit]}


@router.get("/projects/{pid}/artifacts/{key}/file")
def artifact_raw_path(pid: str, key: str):
    art = db.get_artifact(pid, key)
    if not art:
        raise HTTPException(404, "不存在")
    return {"key": key, "path": art.get("path"), "kind": art.get("kind"), "meta": art.get("meta", {})}


@router.get("/projects/{pid}/frames/{idx}/image")
def frame_image(pid: str, idx: int):
    """按序号取关键帧图片（浏览器不能直接读服务器本地路径，所以走这个接口）。"""
    frames = _load_json_artifact(pid, "frames") or []
    for f in frames:
        if int(f.get("idx", -1)) == idx:
            p = Path(f.get("path", ""))
            if p.exists():
                return FileResponse(str(p), media_type="image/jpeg")
            raise HTTPException(404, "关键帧文件已丢失")
    raise HTTPException(404, "没有这一帧")


# --------------------------------------------------------------------------
def _load_json_artifact(pid: str, key: str):
    import json

    p = _path_of(pid, key)
    if not p or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _path_of(pid: str, key: str) -> Optional[Path]:
    art = db.get_artifact(pid, key)
    if not art or not art.get("path"):
        return None
    return Path(art["path"])
