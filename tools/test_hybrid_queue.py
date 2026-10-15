# -*- coding: utf-8 -*-
"""统一复习队列自检：句子卡 + 单词卡混排（不需要启动服务，直接调内部模块）。

重点验证四件事：
  1. 单词练砸了会自动进队列，练好了不会（跟句子的规则一致）；
  2. 关键音读错必须把分数压到及格线下 —— 否则整词 accuracy 会把它掩盖掉；
  3. 混排的比率和「各自内部顺序不变」；
  4. 单词卡不会污染句子卡（card_type / 字段契约）。

这个脚本**完全零痕迹**：先快照再还原 word_reviews，练习流水只删自己新增的行，
并且挑一个当前没有卡片的词对来测。跑完你的复习进度和统计跟跑之前一模一样。

用法： .venv\\Scripts\\python.exe tools\\test_hybrid_queue.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                                       # noqa: E402
from videonote.core import analytics, minimal_pairs as MP, srs  # noqa: E402

FAIL = 0

WORD_COLS = ("id", "pair_id", "word", "ease", "interval_days", "reps", "lapses",
             "due_at", "last_at", "last_score", "best_score", "reason", "status", "created_at")


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def restore(snap: list[dict], drill_max: int = 0) -> None:
    """恢复成测试前的样子：删掉新建的、还原被改的、复活被误删的。

    练习流水（pair_drills）只删掉本脚本新增的行（id > 测试前的最大 id），
    这样自测不会在你的真实统计里留下「假练习」。
    """
    # drill_max=0（表本来是空的）时 id>0 正好删掉本次新增的全部行
    db.execute("DELETE FROM pair_drills WHERE id > ?", (drill_max,))
    keep = {int(r["id"]) for r in snap}
    for row in db.list_word_reviews(limit=99999):
        if int(row["id"]) not in keep:
            db.delete_word_review(int(row["id"]))
    for row in snap:
        fields = {k: row[k] for k in db.WORD_REVIEW_FIELDS if k in row}
        if db.get_word_review(int(row["id"])):
            db.update_word_review(int(row["id"]), **fields)
        else:
            db.execute(
                f"INSERT INTO word_reviews ({','.join(WORD_COLS)}) VALUES ({','.join('?' * len(WORD_COLS))})",
                tuple(row.get(c) for c in WORD_COLS),
            )


def main() -> int:
    db.init()
    snap = [dict(r) for r in db.list_word_reviews(limit=99999)]
    drill_max = int((db.query_one("SELECT COALESCE(MAX(id),0) AS m FROM pair_drills") or {}).get("m") or 0)
    print(f"测试前已有单词卡：{len(snap)} 张，练习流水最大 id={drill_max}")

    # 挑一个两边都还没有卡片的词对，这样整个测试不碰用户已有进度
    pair = next((c for c in MP.PAIRS
                 if not db.find_word_review(c["id"], c["a"]) and not db.find_word_review(c["id"], c["b"])),
                None)
    if pair is None:
        print("❌ 35 个词对全都已经有复习卡了，没法安全地做这个自检")
        return 1
    pid_, a, b = pair["id"], pair["a"], pair["b"]
    print(f"用词对 {pid_}（{a} / {b}，{pair['pa']} vs {pair['pb']}）做测试\n")

    try:
        # ---------- 1. 关键音决定分数 ----------
        print("— 分数换算 —")
        check("关键音读对 -> 按整词准确率给分", srs.score_word_drill(88, True) == 88.0)
        capped = srs.score_word_drill(90, False)
        check("关键音读错 -> 压到及格线下", capped < srs.REVIEW_THRESHOLD,
              f"accuracy 90 但 key_hit=False -> {capped}")
        check("关键音读错时的上限是 KEY_PHONE_MISS_CAP", capped == srs.KEY_PHONE_MISS_CAP)
        check("关键音读错且本来就更低 -> 取更低那个", srs.score_word_drill(20, False) == 20.0)
        check("听音辨词选对/选错", (srs.score_listen_drill(True), srs.score_listen_drill(False))
              == (srs.LISTEN_CORRECT_SCORE, srs.LISTEN_WRONG_SCORE))

        # ---------- 2. 练砸了才进队列 ----------
        print("\n— 自动入队 —")
        r1 = srs.word_note(pid_, a, 95.0, key_hit=True, mode="say")
        check("一次就读对 -> 不入队", r1 == {}, f"{r1}")
        check("确认库里没有这张卡", db.find_word_review(pid_, a) is None)

        r2 = srs.word_note(pid_, a, srs.score_word_drill(90, False), key_hit=False, mode="say")
        check("关键音读错 -> 建卡", bool(r2.get("queued")) and r2.get("status") == "new", f"{r2}")
        card = db.find_word_review(pid_, a)
        check("建卡后能查到", bool(card))
        check("进队列原因说清了是哪个音", pair["pa"] in (card or {}).get("reason", ""),
              (card or {}).get("reason", ""))
        check("卡片归在 new（今天到期）", srs.is_due(card))

        # ---------- 3. 已有卡会被推进 ----------
        print("\n— 已有卡推进 SM-2 —")
        rid = card["id"]
        r3 = srs.word_note(pid_, a, 95.0, key_hit=True, mode="say")
        check("已有卡：这次读好 -> 往后推", r3.get("interval_days") == 1.0 and r3.get("status") == "review",
              f"interval={r3.get('interval_days')} due={r3.get('next_due')}")
        check("已有卡：不再重复建卡", db.find_word_review(pid_, a)["id"] == rid)
        r4 = srs.word_note(pid_, a, 95.0, key_hit=True, mode="say")
        check("连续两次读好 -> 间隔 3 天", r4.get("interval_days") == 3.0, f"{r4.get('interval_days')}")
        r5 = srs.word_note(pid_, a, srs.score_word_drill(95, False), key_hit=False, mode="say")
        check("又读错 -> 打回 learning", r5.get("status") == "learning", f"{r5.get('status')}")

        # ---------- 4. 听音辨词也是对同一个词的证据 ----------
        print("\n— 听音辨词 —")
        r6 = srs.word_note(pid_, b, srs.score_listen_drill(False), key_hit=False, mode="listen")
        check("听音辨词选错 -> 建卡", bool(r6.get("queued")), f"{r6}")
        check("原因是「听音辨词没分清」", "听音辨词" in (db.find_word_review(pid_, b) or {}).get("reason", ""),
              (db.find_word_review(pid_, b) or {}).get("reason", ""))
        r7 = srs.word_note(pid_, b, srs.score_listen_drill(True), key_hit=True, mode="listen")
        check("下次听对了 -> 卡片往后推", r7.get("status") == "review", f"{r7}")
        check("两个词是两张独立的卡",
              db.find_word_review(pid_, a)["id"] != db.find_word_review(pid_, b)["id"])

        # ---------- 5. grade 同时写流水 ----------
        print("\n— 复习页打分 —")
        before_drills = len(db.list_pair_drills(limit=99999))
        g = srs.word_grade(rid, 95.0, seconds=1.5, kind="review")
        check("grade 返回新调度", "due_at" in g and g.get("last_score") == 95.0, f"due={g.get('due_at')}")
        check("grade 写了 pair_drills 流水",
              len(db.list_pair_drills(limit=99999)) == before_drills + 1)
        g2 = srs.word_grade(rid, 30.0, seconds=1.0)
        check("grade 低分 -> 打回 learning", g2.get("status") == "learning", f"{g2.get('status')}")

        # ---------- 6. 队列与 enrich ----------
        print("\n— 队列内容 —")
        srs.word_add(pid_, a, "自检：强制造一张到期的卡")   # 让这张卡今天到期，才测得到 due_queue
        wq = srs.word_due_queue(limit=20)
        check("到期队列里有卡", len(wq) >= 1, f"{len(wq)} 张")
        if wq:
            it = wq[0]
            check("带 card_type=word", it.get("card_type") == "word")
            check("带 word / text（前端可统一渲染）", bool(it.get("word")) and it.get("text") == it["word"])
            check("带音频地址", str(it.get("audio_url", "")).startswith("/api/words/audio?word="),
                  it.get("audio_url", ""))
            check("带关键音素", bool(it.get("key_phone")), f"/{it.get('key_phone')}/")
            check("带对照词", bool(it.get("other")), f"{it.get('word')} vs {it.get('other')}")
            check("带嘴型提示", bool(it.get("tip")))
            check("带调度字段", "due_at" in it and "ease" in it and "reps" in it)

        wc = srs.word_counts()
        check("word_counts 字段齐全", set(wc) >= {"due", "total", "learning", "reviewing", "new"},
              f"{wc}")
        check("due_queue 只给到期的", all(srs.is_due(x) for x in wq))
        future = srs.word_due_queue(limit=50, include_future=True)
        check("include_future 能带出未到期的", len(future) >= len(wq), f"{len(future)} >= {len(wq)}")

        # ---------- 7. 混排 ----------
        print("\n— 混排 —")
        sent = [{"id": f"s{i}", "card_type": "sentence"} for i in range(6)]
        word = [{"id": f"w{i}", "card_type": "word"} for i in range(3)]
        mixed = srs._interleave(sent, word, 1 / 3)
        kinds = [x["card_type"] for x in mixed]
        check("交错后总数不变", len(mixed) == 9, f"{len(mixed)}")
        check("句子内部顺序不变",
              [x["id"] for x in mixed if x["card_type"] == "sentence"] == [f"s{i}" for i in range(6)])
        check("单词内部顺序不变",
              [x["id"] for x in mixed if x["card_type"] == "word"] == [f"w{i}" for i in range(3)])
        check("不是先堆一堆单词（第 1 张是句子）", kinds[0] == "sentence")
        check("约每 2 句夹 1 个词", kinds.index("word") == 2, f"前 5 张：{kinds[:5]}")
        check("单词不会被挤到最后", kinds.index("word") < len(kinds) - 3)
        check("句子用完后就只放剩下的单词",
              [x["card_type"] for x in srs._interleave(sent[:1], word, 1 / 3)] ==
              ["sentence", "word", "word", "word"])
        check("空输入不炸", srs._interleave([], [], 1 / 3) == [])
        check("ratio=0 时全是句子",
              all(x["card_type"] == "sentence" for x in srs._interleave(sent, word, 0)))
        check("句子为空时只给单词",
              [x["card_type"] for x in srs._interleave([], word, 1 / 3)] == ["word"] * 3)

        # ---------- 8. 统一队列不污染句子卡 ----------
        print("\n— 统一队列 —")
        uq = srs.unified_queue(limit=10)
        check("unified_queue 返回列表", isinstance(uq, list), f"{len(uq)} 张")
        check("每张卡都有 card_type", all(x.get("card_type") in ("sentence", "word") for x in uq))
        check("至少带出了刚造的单词卡", any(x.get("card_type") == "word" for x in uq))
        sq = srs.due_queue(limit=10)
        check("句子队列仍然只含句子", all(x.get("card_type") == "sentence" for x in sq))
        check("句子卡原有字段未被改动",
              all({"seg_idx", "project_id", "text", "audio_url"} <= set(x) for x in sq),
              f"{len(sq)} 张句子卡")
        check("include_words=False 时退回纯句子队列",
              all(x.get("card_type") == "sentence"
                  for x in srs.unified_queue(limit=10, include_words=False)))
        uc = srs.unified_counts()
        check("合并计数 = 句子 + 单词",
              uc["due"] == uc["due_sentences"] + uc["due_words"],
              f"{uc['due_sentences']} + {uc['due_words']} = {uc['due']}")
        check("合并计数保留句子原字段", "reviewing" in uc and "words" in uc)
        check("counts() 本身没被污染（仍是句子口径）", "words" not in srs.counts())

        # ---------- 9. 同步是幂等的 ----------
        print("\n— 历史回填 —")
        s1 = srs.word_sync()
        s2 = srs.word_sync()
        check("第一次同步会扫历史流水", s1["scanned"] > 0, f"{s1}")
        check("第二次同步不再重复建（幂等）", s2["created"] == 0, f"{s2}")

        # ---------- 10. 统计 ----------
        print("\n— 统计 —")
        wr = analytics.word_report()
        check("word_report 有 kinds/hardest/queue",
              {"kinds", "hardest", "queue", "pairs_total"} <= set(wr), f"练过 {wr['pairs_practiced']} 对")
        check("pairs_total 是词库总数", wr["pairs_total"] == len(MP.PAIRS), f"{wr['pairs_total']}")
        if wr["hardest"]:
            h = wr["hardest"][0]
            check("hardest 升序（最差在最前）",
                  all(wr["hardest"][i]["ok_rate"] <= wr["hardest"][i + 1]["ok_rate"]
                      for i in range(len(wr["hardest"]) - 1)),
                  f"最差 {h['a']}/{h['b']} 正确率 {h['ok_rate']}%")
        rep = analytics.report()
        ov = rep["overview"]
        check("overview 纳入单词练习", ov["word_attempts"] > 0 and "word_ok_rate" in ov,
              f"词 {ov['word_attempts']} 次 / 正确率 {ov['word_ok_rate']}%")
        check("report 有 words 段", "words" in rep)
        check("daily 带 words 字段", "words" in rep["daily"][0])
        check("单词练习计入活跃天数", ov["active_days"] >= 1)
        check("单词统计是全局的（单词不绑项目）",
              analytics.report(db.list_projects()[0]["id"])["words"]["attempts"] == wr["attempts"]
              if db.list_projects() else True)

    finally:
        restore(snap, drill_max)
        now = {int(r["id"]) for r in db.list_word_reviews(limit=99999)}
        left = int((db.query_one("SELECT COALESCE(MAX(id),0) AS m FROM pair_drills") or {}).get("m") or 0)
        print(f"\n已还原 word_reviews 到测试前的 {len(snap)} 张")
        check("还原后没有多余的卡、也没少卡", now == {int(r["id"]) for r in snap},
              f"现在 {sorted(now)} vs 之前 {sorted(int(r['id']) for r in snap)}")
        check("没在练习流水里留下假练习", left <= drill_max, f"最大 id {left} <= {drill_max}")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ 统一复习队列（句子 + 单词）全部通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
