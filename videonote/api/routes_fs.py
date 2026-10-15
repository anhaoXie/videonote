# -*- coding: utf-8 -*-
"""本机文件浏览接口 —— 让「导入本机视频」可以直接点着选，不用手敲路径。

只读、只列目录和媒体文件，不做任何写操作。
"""
from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ..core.media import AUDIO_EXT, VIDEO_EXT
from ..core.registry import register_router

router = APIRouter(tags=["fs"])

MEDIA_EXT = VIDEO_EXT | AUDIO_EXT

# 常见视频位置，方便一键跳过去
QUICK = [
    ("下载", Path.home() / "Downloads"),
    ("桌面", Path.home() / "Desktop"),
    ("视频", Path.home() / "Videos"),
    ("文档", Path.home() / "Documents"),
    ("此电脑", None),
]


def _drives() -> List[Dict[str, str]]:
    out = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        try:
            if root.exists():
                out.append({"name": f"{letter}:", "path": str(root)})
        except OSError:
            continue
    return out


def _quick_list() -> List[Dict[str, str]]:
    out = []
    for label, p in QUICK:
        if p is None:
            out.append({"name": "此电脑（所有磁盘）", "path": ""})
        elif p.exists():
            out.append({"name": label, "path": str(p)})
    return out


def _safe(p: str) -> Path:
    """规范化路径，顺手挡掉一些明显的怪输入。"""
    p = (p or "").strip().strip('"')
    if "\x00" in p:
        raise HTTPException(400, "路径不合法")
    return Path(os.path.expandvars(os.path.expanduser(p)))


@router.get("/fs/list")
def list_dir(path: str = "") -> Dict[str, Any]:
    if not path:
        return {"path": "", "parent": None, "is_root": True,
                "dirs": _drives(), "files": [], "quick": _quick_list()}

    target = _safe(path)
    if not target.exists():
        raise HTTPException(404, f"路径不存在：{target}")
    if target.is_file():
        target = target.parent

    dirs: List[Dict[str, str]] = []
    files: List[Dict[str, Any]] = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                name = entry.name
                if name.startswith("$") or name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": name, "path": entry.path})
                    elif entry.is_file(follow_symlinks=False):
                        ext = Path(name).suffix.lower()
                        if ext in MEDIA_EXT:
                            st = entry.stat()
                            files.append({"name": name, "path": entry.path,
                                          "size": st.st_size, "ext": ext,
                                          "kind": "video" if ext in VIDEO_EXT else "audio"})
                except OSError:
                    continue
    except PermissionError as exc:
        raise HTTPException(403, f"没有权限读取这个目录：{target}") from exc
    except OSError as exc:
        raise HTTPException(400, f"读取失败：{exc}") from exc

    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    parent = str(target.parent) if target.parent != target else ""
    return {"path": str(target), "parent": parent, "is_root": False,
            "dirs": dirs, "files": files, "quick": _quick_list()}


@router.get("/fs/info")
def file_info(path: str) -> Dict[str, Any]:
    """确认某个路径存在、是不是媒体文件、多大。导入前先看一眼。"""
    p = _safe(path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    if p.is_dir():
        return {"exists": True, "path": str(p), "is_dir": True}
    ext = p.suffix.lower()
    return {
        "exists": True, "path": str(p), "is_dir": False,
        "name": p.name, "size": p.stat().st_size, "ext": ext,
        "is_media": ext in MEDIA_EXT,
    }


register_router(router)
