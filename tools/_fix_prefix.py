# -*- coding: utf-8 -*-
"""一次性小工具：把测试脚本里的接口路径补上 /api 前缀。"""
from pathlib import Path

OLD = '"/words/'
NEW = '"/api/words/'

for name in ("tools/test_words.py",):
    p = Path(name)
    t = p.read_text(encoding="utf-8")
    n = t.count(OLD)
    p.write_text(t.replace(OLD, NEW), encoding="utf-8")
    print(f"{name}: 替换 {n} 处")
