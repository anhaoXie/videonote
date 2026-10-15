# -*- coding: utf-8 -*-
"""清理历史评分里「非英文句子」的音素明细。

背景：音素评测早先没做语言判断，中文视频里老师随口念的字母（k / n）也被当成
「该发的音」，统计页的「最常读错的音」就被这种噪声占满了。
现在评分接口只对英文内容做音素评测；这个脚本负责把**历史数据**里同样的噪声删掉，
只删 phoneme_words / phoneme_errors 两个字段，评分本身不动。

用法：
    .venv\\Scripts\\python.exe tools\\clean_phoneme_noise.py          # 只看会改什么
    .venv\\Scripts\\python.exe tools\\clean_phoneme_noise.py --yes    # 真删
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db            # noqa: E402
from videonote.models import dumps  # noqa: E402


def english_ratio(text: str) -> float:
    chars = sum(1 for c in (text or "") if not c.isspace())
    if not chars:
        return 0.0
    letters = sum(1 for c in text if c.isascii() and c.isalpha())
    return letters / chars


def main() -> int:
    apply = "--yes" in sys.argv
    db.init()
    rows = db.query("SELECT id, project_id, seg_idx, target, detail FROM scores")
    dirty = []
    for r in rows:
        detail = db.loads(r.get("detail"), {})
        has = bool(detail.get("phoneme_words")) or bool(detail.get("phoneme_errors"))
        if not has:
            continue
        if english_ratio(r.get("target") or "") < 0.5:
            dirty.append((r["id"], r.get("target") or "", len(detail.get("phoneme_errors") or [])))

    print(f"共 {len(rows)} 条评分记录，其中 {len(dirty)} 条含「非英文句子」的音素明细：")
    for sid, target, n in dirty[:12]:
        print(f"  #{sid}  {target[:38]!r}  ({n} 条音素错误)")
    if len(dirty) > 12:
        print(f"  … 还有 {len(dirty) - 12} 条")

    if not dirty:
        print("没什么可清的 ✅")
        return 0
    if not apply:
        print("\n这是预演。加 --yes 才会真的清理。")
        return 0

    for sid, _target, _n in dirty:
        row = db.query_one("SELECT detail FROM scores WHERE id=?", (sid,))
        detail = db.loads(row.get("detail"), {})
        detail.pop("phoneme_words", None)
        detail.pop("phoneme_errors", None)
        detail.pop("phoneme_seconds", None)
        db.execute("UPDATE scores SET detail=? WHERE id=?", (dumps(detail), sid))
    print(f"\n✅ 已清理 {len(dirty)} 条记录的音素明细（评分和逐词结果都保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
