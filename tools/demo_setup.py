# -*- coding: utf-8 -*-
"""一键准备演示项目：生成测试视频 -> 完整分析 -> 说话人分离 -> 收藏一句进句库。

    .venv\\Scripts\\python.exe tools\\demo_setup.py

跑完打开 http://127.0.0.1:8756 就能看到：转写 / 笔记 / 关键帧 / 字幕 / 说话人 / 跟读句库，
不用自己先找视频。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8756"
VIDEO = ROOT / "samples" / "test_video.mp4"


def wait(c: httpx.Client, jid: str, timeout: float = 1800) -> dict:
    t0 = time.time()
    last = None
    while True:
        j = c.get(f"/api/jobs/{jid}").json()["job"]
        cur = f'{j["status"]} {j["progress"]:.0f}% {j.get("message","")}'
        if cur != last:
            print(f"  [{time.time()-t0:6.1f}s] {cur}")
            last = cur
        if j["status"] in ("done", "failed", "canceled"):
            return j
        if time.time() - t0 > timeout:
            raise TimeoutError("任务超时")
        time.sleep(2)


def main() -> int:
    if not VIDEO.exists():
        print("没有测试视频，先生成…")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_test_video.py"), "--sample"], check=True)

    c = httpx.Client(base_url=BASE, timeout=1800)
    print("\n1/3 导入 + 完整分析…")
    res = c.post("/api/projects", json={"title": "演示：JFK 演讲片段", "source_type": "file",
                                        "path": str(VIDEO), "auto_start": True,
                                        "job_kind": "analyze"}).json()
    pid = res["project"]["id"]
    if wait(c, res["job"]["id"])["status"] != "done":
        print("分析失败")
        return 1

    print("\n2/3 说话人分离…")
    r = c.post(f"/api/projects/{pid}/jobs", json={"kind": "diarize", "params": {}})
    if r.status_code == 200:
        wait(c, r.json()["job"]["id"])
    else:
        print("  跳过（", r.text[:120], "）")

    print("\n3/3 收藏第一句进句库…")
    segs = c.get(f"/api/projects/{pid}/segments").json()["segments"]
    if segs:
        c.post(f"/api/projects/{pid}/clips", json={"seg_idx": segs[0]["idx"], "tags": "演示",
                                                   "note": "点「🎙 跟读」录一遍，再点「🎯 评分」试试"})

    detail = c.get(f"/api/projects/{pid}").json()
    print("\n产出：")
    for a in detail["artifacts"]:
        print(f'  - {a["key"]}')
    print(f'\n完成！打开 http://127.0.0.1:8756/ 选择「演示：JFK 演讲片段」')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
