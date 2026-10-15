# -*- coding: utf-8 -*-
"""用真 key 跑一遍大模型链路：摘要笔记 + 双语字幕翻译。

    .venv\\Scripts\\python.exe tools\\test_llm_pipeline.py

它会挑一个项目（默认第一个），强制重跑 notes，再跑 translate，
最后把生成的笔记片段和双语字幕样例打印出来。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8756"


def wait_job(c: httpx.Client, jid: str, timeout: float = 900) -> dict:
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
        time.sleep(1.5)


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=900)
    projects = c.get("/api/projects").json()["projects"]
    if not projects:
        print("没有项目")
        return 1
    pid = projects[0]["id"]
    print("项目:", projects[0]["title"], pid)

    print("\n=== 1) 大模型摘要笔记（强制重跑）===")
    r = c.post(f"/api/projects/{pid}/jobs", json={"kind": "summarize", "params": {"force": ["notes"]}})
    if r.status_code != 200:
        print("发起失败:", r.text[:200])
        return 1
    j = wait_job(c, r.json()["job"]["id"])
    if j["status"] != "done":
        print("失败日志:")
        print("\n".join(l["message"] for l in c.get(f"/api/jobs/{j['id']}").json()["logs"][-8:]))
        return 1

    notes = c.get(f"/api/projects/{pid}/notes").json()
    print(f'\n摘要引擎: {(notes.get("summary") or {}).get("_engine")}')
    print("-" * 60)
    print(notes["markdown"][:1800])
    print("-" * 60)

    print("\n=== 2) 双语字幕翻译 ===")
    r2 = c.post(f"/api/projects/{pid}/jobs", json={"kind": "translate", "params": {}})
    if r2.status_code != 200:
        print("发起失败:", r2.text[:200])
        return 1
    j2 = wait_job(c, r2.json()["job"]["id"])
    if j2["status"] != "done":
        print("失败日志:")
        print("\n".join(l["message"] for l in c.get(f"/api/jobs/{j2['id']}").json()["logs"][-8:]))
        return 1

    srt = c.get(f"/api/projects/{pid}/artifact/subtitles_srt").text
    print("\n双语字幕前 20 行:")
    print("\n".join(srt.splitlines()[:20]))
    print("\n✅ 大模型链路（摘要 + 翻译）全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
