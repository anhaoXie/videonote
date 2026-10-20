# -*- coding: utf-8 -*-
"""课程生成任务：不隶属于任何项目，走独立执行器。

复用 jobs 表（project_id 可空）和同一个 worker，好处是排队、取消、进度、
日志全部沿用现成机制，不用再造一套后台线程。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .. import db
from ..models import JobStatus

KIND = "course_build"
PREFIX = "course"  # worker 用前缀判断该走哪个执行器


def is_course_job(kind: str) -> bool:
    return str(kind or "").startswith(PREFIX)


def run(job: dict) -> Dict[str, Any]:
    from . import coursegen as CG
    from . import events

    jid = job["id"]
    p = job.get("params") or {}

    source = (p.get("source") or "").strip()
    if not source:
        raise ValueError("缺少素材：请给一段文本或一个主题")

    episodes = max(1, int(p.get("episodes") or 3))
    minutes = max(1, int(p.get("minutes") or 3))
    wpm = int(p.get("wpm") or 150)
    out_root = Path(p.get("out") or "data/courses")
    only = int(p.get("only") or 1)
    want_all = bool(p.get("all", True))

    def prog(pct: float, msg: str) -> None:
        db.update_job(jid, progress=max(0, min(99, int(pct))), message=msg)
        events.notify(jid, None, JobStatus.RUNNING, msg)

    prog(2, "生成分集大纲…")
    outline = CG.plan_outline(source, episodes, minutes)
    eps = outline.get("episodes") or []
    if not eps:
        raise RuntimeError("大纲为空，无法继续")

    targets = eps if want_all else [e for e in eps if int(e.get("no", 0)) == only]
    if not targets:
        raise RuntimeError(f"大纲里没有第 {only} 集")

    title = outline.get("title") or "未命名课程"
    # 每次生成一个独立目录，避免按集号互相覆盖
    course_dir = CG.new_course_dir(out_root, title)
    db.add_log(jid, f"课程大纲：{title}，共 {len(targets)} 集待生成（目录 {course_dir.name}）", "info")
    made: list = []

    done = []
    total = len(targets)
    for i, e in enumerate(targets, 1):
        no = int(e.get("no", i))
        e["course"] = title
        base = (i - 1) / total

        def sub(pct: float, msg: str, base: float = base, i: int = i) -> None:
            inner = max(0.0, min(100.0, float(pct))) / 100.0
            prog((base + inner / total) * 100.0, f"[{i}/{total}] {msg}")

        prog(base * 100 + 1, f"[{i}/{total}] 第 {no} 集：{e.get('title', '')}")
        try:
            info = CG.make_episode(e, course_dir / f"ep{no:02d}", source=source,
                                   wpm=wpm, progress=sub)
            info["episode"] = no
            info["title"] = e.get("title", "")
            done.append(info)
            made.append({
                "no": no,
                "title": e.get("title", ""),
                "file": f"ep{no:02d}/episode.mp4",
                "seconds": info.get("seconds"),
            })
        except Exception as exc:  # noqa: BLE001
            # 单集失败不拖垮整批，但要在日志里留证据
            db.add_log(jid, f"第 {no} 集失败：{type(exc).__name__}: {exc}", "error")
            done.append({"episode": no, "title": e.get("title", ""),
                         "error": f"{type(exc).__name__}: {exc}"})
        # 每完成一集就把清单落盘：中途中断也能看到已完成的部分
        CG.write_manifest(course_dir, title, source, made)

    ok = [d for d in done if d.get("video")]
    return {
        "course": title,
        "course_dir": course_dir.name,
        "episodes": done,
        "count": len(done),
        "ok": len(ok),
        "seconds": round(sum(d.get("seconds", 0) for d in ok), 1),
    }
