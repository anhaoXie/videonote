# -*- coding: utf-8 -*-
"""排查 espeakng-loader 的库路径与数据目录。"""
from pathlib import Path

import espeakng_loader as E

print("包目录:", Path(E.__file__).parent)
print("get_library_path():", E.get_library_path())
try:
    print("get_data_path():", E.get_data_path())
except Exception as exc:  # noqa: BLE001
    print("get_data_path 失败:", exc)

root = Path(E.__file__).parent
print("\n包内文件（前 40 个）:")
files = sorted(root.rglob("*"))
for p in files[:40]:
    kind = "dir " if p.is_dir() else "file"
    print(f"  {kind} {p.relative_to(root)}")
print(f"... 共 {len(files)} 项")

print("\n找 phontab / espeak-ng-data:")
for p in root.rglob("phontab"):
    print("  ★", p)
for p in root.rglob("espeak-ng-data"):
    print("  ★", p)
