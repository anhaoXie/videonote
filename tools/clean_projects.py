# -*- coding: utf-8 -*-
"""清理重复项目：同名项目只保留最新的一个（其余连文件一起删）。

    .venv\\Scripts\\python.exe tools\\clean_projects.py            # 先看会删什么
    .venv\\Scripts\\python.exe tools\\clean_projects.py --yes      # 真的删
"""
from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8756"


def main() -> int:
    apply = "--yes" in sys.argv
    c = httpx.Client(base_url=BASE, timeout=120)
    projects = c.get("/api/projects").json()["projects"]
    seen: set[str] = set()
    doomed = []
    for p in projects:                       # 列表按创建时间倒序
        if p["title"] in seen:
            doomed.append(p)
        else:
            seen.add(p["title"])
    if not doomed:
        print("没有重复项目")
    for p in doomed:
        print(f'{"删除" if apply else "将删除"}: {p["title"]}  {p["id"]}')
        if apply:
            c.delete(f"/api/projects/{p['id']}?delete_files=true")
    print("\n保留:")
    for p in c.get("/api/projects").json()["projects"]:
        print(f'  {p["title"]}  {p["id"]}  产出={p.get("artifact_keys")}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
