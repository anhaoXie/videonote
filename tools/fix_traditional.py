# -*- coding: utf-8 -*-
"""把已有项目里的繁体字就地转成简体（不重跑 ASR）。

为什么需要这个工具：
    繁体是 **Whisper 识别中文时直接吐出来的**，字幕文本就来自转写结果。
    修好代码之后，新识别的视频自然是简体；但已经识别过的项目，
    重跑 ASR 要一两小时——这个工具直接把 segments 表和转写 JSON 里的
    文本就地转换，几秒钟搞定。

安全性：
    - 默认 **dry-run**，只报告会改多少，不落盘；
    - 加 --apply 才写，写之前先把数据库备份到 data/backups/；
    - 只动文本，不动时间轴、说话人、句子编号；
    - 英文内容经过转换是原样返回，不会被改坏。

用法：
    .\\.venv\\Scripts\\python.exe tools\\fix_traditional.py            # 先看会改什么
    .\\.venv\\Scripts\\python.exe tools\\fix_traditional.py --apply    # 真的改
    .\\.venv\\Scripts\\python.exe tools\\fix_traditional.py --project p_xxx --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videonote import db  # noqa: E402
from videonote.config import settings  # noqa: E402
from videonote.core.zh import available, has_traditional, to_simplified  # noqa: E402


def backup_db() -> Path:
    """用文件复制备份数据库（含 WAL 时更稳妥的是 sqlite backup，这里保持简单）。"""
    bdir = Path(settings.data_dir) / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    src = Path(settings.data_dir) / "videonote.db"
    dst = bdir / f"videonote-{datetime.now():%Y%m%d-%H%M%S}-fixzh.db"
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        extra = Path(str(src) + suffix)
        if extra.exists():
            shutil.copy2(extra, Path(str(dst) + suffix))
    return dst


def fix_segments(pid: str, apply: bool) -> tuple[int, int]:
    segs = db.list_segments(pid)
    changed = 0
    samples: list[tuple[str, str]] = []
    for s in segs:
        raw = (s.get("text") or "")
        simp = to_simplified(raw)
        if simp != raw:
            changed += 1
            if len(samples) < 3:
                samples.append((raw, simp))
            s["text"] = simp
    if apply and changed:
        db.replace_segments(pid, segs)
    for raw, simp in samples:
        print(f"      · {raw[:38]}")
        print(f"        -> {simp[:38]}")
    return changed, len(segs)


def fix_transcript_json(pid: str, apply: bool) -> int:
    art = db.get_artifact(pid, "transcript")
    if not art or not art.get("path"):
        return 0
    p = Path(art["path"])
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"      ! 转写 JSON 读不出来，跳过：{exc}")
        return 0
    segs = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segs, list):
        return 0
    n = 0
    for s in segs:
        if not isinstance(s, dict):
            continue
        raw = s.get("text") or ""
        simp = to_simplified(raw)
        if simp != raw:
            s["text"] = simp
            n += 1
        for w in (s.get("words") or []):
            if isinstance(w, dict) and isinstance(w.get("word"), str):
                w["word"] = to_simplified(w["word"])
    if apply and n:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def fix_transcript_txt(pid: str, apply: bool) -> int:
    art = db.get_artifact(pid, "transcript_text")
    if not art or not art.get("path"):
        return 0
    p = Path(art["path"])
    if not p.exists():
        return 0
    raw = p.read_text(encoding="utf-8")
    simp = to_simplified(raw)
    if simp != raw:
        if apply:
            p.write_text(simp, encoding="utf-8")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写入（默认只报告）")
    ap.add_argument("--project", default="", help="只处理某个项目 id")
    args = ap.parse_args()

    if not available():
        print("❌ opencc 不可用，无法转换。先装：pip install opencc-python-reimplemented")
        return 1

    projects = db.list_projects()
    if args.project:
        projects = [p for p in projects if p["id"] == args.project]
        if not projects:
            print(f"❌ 找不到项目 {args.project}")
            return 1

    mode = "写入" if args.apply else "预览（不会改任何东西）"
    print(f"模式：{mode}\n")

    if args.apply:
        dst = backup_db()
        print(f"数据库已备份：{dst}\n")

    total_changed = 0
    todo: list[dict] = []
    for p in projects:
        pid, title = p["id"], p["title"]
        segs = db.list_segments(pid)
        n_trad = sum(1 for s in segs if has_traditional(s.get("text") or ""))
        if not segs:
            continue
        print(f"{title[:30]}  （{len(segs)} 句）")
        if n_trad == 0:
            print("    ✅ 没有繁体，跳过")
            continue
        print(f"    含繁体 {n_trad} 句")
        changed, total = fix_segments(pid, args.apply)
        n_json = fix_transcript_json(pid, args.apply)
        n_txt = fix_transcript_txt(pid, args.apply)
        print(f"    segments 改动 {changed}/{total} 句；转写 JSON {n_json} 句；转写 TXT {'改了' if n_txt else '无需改'}")
        total_changed += changed
        todo.append({"id": pid, "title": title})
        print()

    print("─" * 60)
    if args.apply:
        print(f"完成：共修正 {total_changed} 句。")
        if todo:
            print("\n还需要重跑「生成字幕」才能更新 SRT/VTT 文件（几秒，不涉及 ASR）：")
            for t in todo:
                print(f"  POST /api/projects/{t['id']}/jobs  {{\"kind\":\"subtitles\"}}")
    else:
        print(f"预览：会修正 {total_changed} 句。确认无误后加 --apply 真正执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
