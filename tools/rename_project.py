# -*- coding: utf-8 -*-
"""改项目名 / 删项目 / 列项目。

    .venv\\Scripts\\python.exe tools\\rename_project.py <项目ID或序号> "新名字"
    .venv\\Scripts\\python.exe tools\\rename_project.py --list
"""
from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8756"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=60)
    projects = c.get("/api/projects").json()["projects"]

    if len(sys.argv) < 2 or sys.argv[1] in ("--list", "-l"):
        for i, p in enumerate(projects, 1):
            print(f"{i}. {p['title']}  [{p['id']}]  {p['duration']:.0f}s")
        return 0

    key = sys.argv[1]
    if len(sys.argv) < 3:
        print("用法: rename_project.py <项目ID或序号> \"新名字\"")
        return 1
    title = sys.argv[2]

    pid = key
    if key.isdigit() and 1 <= int(key) <= len(projects):
        pid = projects[int(key) - 1]["id"]

    r = c.request("PATCH", f"/api/projects/{pid}", data={"title": title})
    if r.status_code == 200:
        print("已改名:", r.json()["project"]["title"])
        return 0
    print(f"失败 {r.status_code}: {r.text[:200]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
