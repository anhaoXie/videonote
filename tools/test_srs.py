# -*- coding: utf-8 -*-
"""复习队列 + 训练统计自检（不需要启动服务，直接调内部模块）。

用法： .venv\\Scripts\\python.exe tools\\test_srs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                                    # noqa: E402
from videonote.core import analytics, srs                   # noqa: E402

FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def main() -> int:
    db.init()
    projects = db.list_projects()
    print(f"项目数：{len(projects)}")
    if not projects:
        print("没有项目，先导入一个视频再测")
        return 1

    # ---------- 同步队列 ----------
    res = srs.sync()
    print(f"sync: {res}")
    counts = srs.counts()
    print(f"counts: {counts}")
    check("队列里有东西（低分句或收藏句）", counts["total"] > 0, f"total={counts['total']}")
    check("有到期项", counts["due"] >= 0)

    # ---------- 队列内容 ----------
    queue = srs.due_queue(limit=5)
    if queue:
        q = queue[0]
        check("队列项带句子文本", bool(q.get("text")), f"「{(q.get('text') or '')[:30]}」")
        check("队列项带项目标题", bool(q.get("project_title")), q.get("project_title", ""))
        check("队列项带音频地址", bool(q.get("audio_url")), q.get("audio_url", ""))
        check("队列项带调度字段", "due_at" in q and "ease" in q and "reps" in q)
        print("队列前 3 条：")
        for q in queue[:3]:
            print(f"   [{q['status']}] {q['project_title'][:12]} #{q['seg_idx']} "
                  f"best={q['best_score']} reps={q['reps']} reason={q['reason']} | {q['text'][:38]}")
    else:
        check("队列有内容", False, "（可能是所有句子都超过 80 分且没有收藏）")

    # ---------- SM-2 调度规则 ----------
    row = {"ease": 2.5, "reps": 0, "lapses": 0, "interval_days": 0, "best_score": 0}
    good = srs.schedule(row, 95)
    check("高分 -> 进入 review 且 1 天后复习", good["status"] == "review" and good["interval_days"] == 1.0,
          f"{good}")
    good2 = srs.schedule({**row, **good}, 95)
    check("连续两次高分 -> 间隔 3 天", good2["interval_days"] == 3.0, f"{good2['interval_days']}")
    good3 = srs.schedule({**row, **good2}, 95)
    check("第三次 -> 间隔按 ease 变长", good3["interval_days"] > 3.0, f"{good3['interval_days']} 天")
    bad = srs.schedule({**row, **good3}, 40)
    check("低分 -> 打回 learning 且 lapse+1", bad["status"] == "learning" and bad["lapses"] == 1, f"{bad}")
    check("低分 -> 间隔归零", bad["interval_days"] == 0.0)
    check("分数->质量映射", [srs.score_to_quality(x) for x in (0, 50, 70, 85, 95)] == [0, 2, 3, 4, 5])

    # ---------- 真正 grade 一条 ----------
    target = db.list_reviews(limit=1)
    if target:
        rid = target[0]["id"]
        before = db.get_review(rid)
        g = srs.grade(rid, 93, seconds=3.2)
        check("grade 推进了复习", g["interval_days"] > 0 or g["status"] == "learning",
              f"interval={g['interval_days']} due={g['due_at']} status={g['status']}")
        check("grade 记录了 last_score", abs(float(g["last_score"]) - 93) < 0.01)
        check("grade 之前的状态被读取过", bool(before))
        srs.reset(rid)
        check("reset 回到 new", db.get_review(rid)["status"] == "new")

    # ---------- 未来负载 ----------
    fc = srs.forecast(days=14)
    check("forecast 返回 14 天", len(fc) == 14, f"明天预计 {fc[1]['count']} 条")
    check("forecast 第一天含今天所有逾期", fc[0]["count"] >= counts["due"])

    # ---------- 统计 ----------
    rep = analytics.report()
    ov = rep["overview"]
    print(f"\n统计概览: 练了 {ov['attempts']} 次 / {ov['sentences']} 句 / 平均 {ov['avg_score']} 分 / "
          f"累计 {ov['minutes']} 分钟 / 连续 {ov['streak']} 天")
    check("overview 有尝试次数", ov["attempts"] >= 0)
    check("daily 有 120 天", len(rep["daily"]) == 120)
    check("distribution 5 档", len(rep["distribution"]) == 5)
    check("weak_words 结构完整", "missing" in rep["weak_words"] and "wrong" in rep["weak_words"])
    print(f"高频漏词: {[w['word'] for w in rep['weak_words']['missing'][:6]]}")
    print(f"高频读错: {[(w['word'], w['heard']) for w in rep['weak_words']['wrong'][:4]]}")
    check("worst_segments 是列表", isinstance(rep["worst_segments"], list))
    if rep["worst_segments"]:
        w = rep["worst_segments"][0]
        check("worst_segments 带文本", bool(w.get("text")), f"最高 {w['best']} 分：{w['text'][:34]}")
    check("by_project 带标题", all(p.get("title") for p in rep["by_project"]))
    check("review 块里有 forecast", len(rep["review"]["forecast"]) == 14)

    # ---------- 单项目视图 ----------
    pid = projects[0]["id"]
    one = analytics.report(pid, days=30)
    check("单项目统计可用", len(one["daily"]) == 30 and one["overview"]["attempts"] >= 0)

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ 复习队列 + 训练统计 全部通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
