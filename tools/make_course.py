# -*- coding: utf-8 -*-
"""把素材（一段文本 / 一个主题 / 一本书的简介）变成分集教学视频。

用法：
    # 最快验证渲染+合成链路（不用大模型、不用联网，约 30 秒）
    .\\.venv\\Scripts\\python.exe tools\\make_course.py --demo

    # 用一段文本/主题生成大纲，再做第 1 集
    .\\.venv\\Scripts\\python.exe tools\\make_course.py --topic "间隔重复为什么有效" --episodes 3 --only 1

    # 用一整个文本文件当素材
    .\\.venv\\Scripts\\python.exe tools\\make_course.py --file book.txt --episodes 6 --only 2

产出：data/courses/<集号>/episode.mp4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videonote.core import coursegen as CG  # noqa: E402

DEMO_EPISODE = {
    "no": 1,
    "title": "演示：间隔重复为什么有效",
    "goal": "理解「在快忘的时候复习」这个机制",
    "course": "学习科学 · 演示",
}

DEMO_LINES = [
    CG.Line(
        say="先问一个问题：为什么有人复习十遍还是忘，有人只复习三次却记住了？",
        slide=CG.Slide(kind="quote", title="复习十遍，还是忘？"),
    ),
    CG.Line(
        say="关键不在次数，而在时间点。在你快要忘记的那一瞬间复习，记忆才会被加固。",
        slide=CG.Slide(kind="bullets", title="不是次数，是时机",
                       bullets=["快要忘记时复习，最有效", "太早复习 = 浪费", "太晚复习 = 重新学"]),
    ),
    CG.Line(
        say="这就是间隔重复：间隔一次比一次长，每次都在临界点上复习。",
        slide=CG.Slide(kind="bullets", title="间隔重复（SM-2）",
                       bullets=["第 1 天 → 第 3 天 → 第 7 天", "答对则间隔乘以系数", "答错就缩短间隔"]),
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="", help="主题或直接一段文本")
    ap.add_argument("--file", default="", help="素材文本文件路径")
    ap.add_argument("--episodes", type=int, default=3, help="分成几集")
    ap.add_argument("--only", type=int, default=1, help="只生成第几集")
    ap.add_argument("--minutes", type=int, default=3, help="每集目标时长（分钟）")
    ap.add_argument("--wpm", type=int, default=150, help="配音语速")
    ap.add_argument("--out", default="data/courses", help="输出目录")
    ap.add_argument("--demo", action="store_true", help="不调大模型，用内置 3 屏演示")
    args = ap.parse_args()

    out_root = Path(args.out)
    t0 = time.time()

    def show(p, m):
        print(f"    {p:5.1f}%  {m}")

    if args.demo:
        print("演示模式：3 屏，验证渲染 + 配音 + 合成\n")
        info = CG.make_episode(DEMO_EPISODE, out_root / "demo-auto",
                               lines=DEMO_LINES, wpm=args.wpm, progress=show)
    else:
        if args.file:
            src = Path(args.file).read_text(encoding="utf-8")
        elif args.topic.strip():
            src = args.topic
        else:
            print("请用 --topic 或 --file 提供素材（或用 --demo 跑演示）")
            return 1

        print("=== 1) 生成大纲 ===")
        outline = CG.plan_outline(src, args.episodes, args.minutes)
        print(f"课程：{outline.get('title', '')}")
        for e in outline.get("episodes") or []:
            print(f"  第 {e.get('no')} 集 · {e.get('title', '')}  —— {e.get('goal', '')}")

        ep = next((e for e in (outline.get("episodes") or [])
                   if int(e.get("no", 0)) == args.only), None)
        if not ep:
            print(f"\n没有第 {args.only} 集（大纲里只有 {len(outline.get('episodes') or [])} 集）")
            return 1
        ep["course"] = outline.get("title", "")

        print(f"\n=== 2) 生成第 {args.only} 集 ===")
        info = CG.make_episode(ep, out_root / f"ep{args.only:02d}", source=src,
                               wpm=args.wpm, progress=show)

    elapsed = time.time() - t0
    print("\n=== 结果 ===")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"\n耗时 {elapsed:.1f} 秒")
    if info.get("seconds"):
        print(f"成片 {info['seconds']:.0f} 秒，平均每秒成片耗时 {elapsed / info['seconds']:.1f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
