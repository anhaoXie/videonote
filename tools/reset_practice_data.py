# -*- coding: utf-8 -*-
"""清空发音训练数据（评分记录 / 训练流水 / 复习队列）。

用途：开发自测会在你的库里留下一堆「假练习」（用参考音频当录音跑出来的分数），
统计页会把它们算成你的练习记录。想让统计只反映你真实的练习，就先清一次。

不会动的东西：项目、转写、笔记、字幕、句库（除非加 --clips）。

用法：
    .venv\\Scripts\\python.exe tools\\reset_practice_data.py            # 预演
    .venv\\Scripts\\python.exe tools\\reset_practice_data.py --yes      # 执行
    .venv\\Scripts\\python.exe tools\\reset_practice_data.py --yes --clips   # 连句库一起清
    .venv\\Scripts\\python.exe tools\\reset_practice_data.py --yes --recordings  # 连录音文件一起删
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                    # noqa: E402
from videonote.config import settings       # noqa: E402


def main() -> int:
    apply = "--yes" in sys.argv
    with_clips = "--clips" in sys.argv
    with_files = "--recordings" in sys.argv
    db.init()

    tables = ["scores", "practice_log", "reviews", "pair_drills", "word_reviews"] + (
        ["clips"] if with_clips else [])
    counts = {}
    for t in tables:
        counts[t] = (db.query_one(f"SELECT COUNT(*) AS n FROM {t}") or {}).get("n", 0)
    print("将要清空：")
    for t, n in counts.items():
        print(f"  {t:14} {n} 行")
    if not apply:
        print("\n这是预演。加 --yes 才会真的执行。")
        return 0

    for t in tables:
        db.execute(f"DELETE FROM {t}")
    print("✅ 已清空")

    if with_files:
        removed = 0
        for p in settings.projects_dir.glob("*/recordings"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        for p in settings.projects_dir.glob("*/seg_audio"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        print(f"✅ 删掉了 {removed} 个录音/切片缓存目录")
    else:
        print("（录音文件保留了。想连文件一起删就加 --recordings）")
    print("\n现在打开「统计」页应该是空的；练第一次之后数据就真实了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
