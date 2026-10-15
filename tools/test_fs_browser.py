# -*- coding: utf-8 -*-
"""自检：内置文件浏览器接口 + 桌面启动器。

    .venv\\Scripts\\python.exe tools\\test_fs_browser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8756"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)

    print("=== 1) 列出所有磁盘 ===")
    d = c.get("/api/fs/list", params={"path": ""}).json()
    print("  磁盘:", ", ".join(x["name"] for x in d["dirs"]))
    print("  快捷键:", ", ".join(x["name"] for x in d["quick"]))
    assert d["dirs"], "没有列出任何磁盘"

    print("\n=== 2) 浏览「下载」文件夹 ===")
    dl = Path.home() / "Downloads"
    d2 = c.get("/api/fs/list", params={"path": str(dl)}).json()
    print(f"  路径: {d2['path']}")
    print(f"  子文件夹 {len(d2['dirs'])} 个，媒体文件 {len(d2['files'])} 个")
    for f in d2["files"][:6]:
        print(f"   🎬 {f['name']}  {f['size']/1048576:.1f} MB")

    print("\n=== 3) 上级目录 / 快捷跳转 ===")
    if d2.get("parent"):
        d3 = c.get("/api/fs/list", params={"path": d2["parent"]}).json()
        print(f"  上级: {d3['path']}")

    print("\n=== 4) 路径信息 ===")
    info = c.get("/api/fs/info", params={"path": str(ROOT / "samples" / "test_video.mp4")}).json()
    print(" ", info)

    print("\n=== 5) 不存在路径要报错而不是崩 ===")
    bad = c.get("/api/fs/info", params={"path": r"Z:\nope\none.mp4"}).json()
    print(" ", bad)

    print("\n=== 6) 插件路由都加载了吗 ===")
    cap = c.get("/api/capabilities").json()
    print("  import_errors:", cap["plugins"]["import_errors"])

    print("\n✅ 文件浏览器自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
