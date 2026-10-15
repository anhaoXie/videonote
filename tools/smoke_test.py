# -*- coding: utf-8 -*-
"""端到端自检：起一个项目 -> 跑完整分析 -> 检查产出。

用法（服务已经在跑的情况下）：
    .venv\\Scripts\\python.exe tools\\smoke_test.py [视频路径]

不传路径就用 tools/make_test_video.py 生成的测试视频。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8756"


def main() -> int:
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "test_video.mp4"
    if not video.exists():
        print(f"找不到测试视频 {video}，先跑 tools\\make_test_video.py")
        return 1

    c = httpx.Client(base_url=BASE, timeout=60.0)
    for i in range(30):
        try:
            if c.get("/api/health").json().get("ok"):
                break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    else:
        print("服务没起来，先运行 run.ps1")
        return 1
    print("服务在线:", c.get("/api/capabilities").json()["version"])

    res = c.post("/api/projects", json={
        "title": f"自检 - {video.stem}",
        "source_type": "file",
        "path": str(video),
        "auto_start": True,
        "job_kind": "analyze",
    }).json()
    pid, job = res["project"]["id"], res["job"]["id"]
    print(f"项目 {pid} / 任务 {job} 已创建，开始轮询…")

    last = None
    t0 = time.time()
    while True:
        j = c.get(f"/api/jobs/{job}").json()["job"]
        cur = f'{j["status"]} {j["progress"]:.0f}% {j.get("message","")}'
        if cur != last:
            print(f'  [{time.time()-t0:6.1f}s] {cur}')
            last = cur
        if j["status"] in ("done", "failed", "canceled"):
            break
        if time.time() - t0 > 3600:
            print("超时")
            return 1
        time.sleep(2)

    if j["status"] != "done":
        logs = c.get(f"/api/jobs/{job}").json()["logs"]
        print("失败详情:")
        print("\n".join(l["message"] for l in logs[-15:]))
        return 1

    print(f"\n耗时 {time.time()-t0:.0f}s。产出检查：")
    detail = c.get(f"/api/projects/{pid}").json()
    for a in detail["artifacts"]:
        size = ""
        p = Path(a["path"]) if a.get("path") else None
        if p and p.exists():
            size = f'{p.stat().st_size/1024:.0f} KB'
        print(f'  - {a["key"]:<18} {a["kind"]:<9} {size}')

    segs = c.get(f"/api/projects/{pid}/segments").json()["segments"]
    print(f"\n识别出 {len(segs)} 句：")
    for s in segs[:8]:
        print(f'  [{s["start"]:6.2f} - {s["end"]:6.2f}] {s["text"]}')

    notes = c.get(f"/api/projects/{pid}/notes").json()
    print(f'\n摘要引擎: {(notes.get("summary") or {}).get("_engine", "-")}')
    print("笔记前 400 字:")
    print(notes["markdown"][:400])

    frames = c.get(f"/api/projects/{pid}/frames").json()
    print(f'\n关键帧: {frames["total"]} 张')
    print("\n打开 http://127.0.0.1:8756/ 选择这个项目即可查看界面。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
