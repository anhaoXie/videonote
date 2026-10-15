# -*- coding: utf-8 -*-
"""按说话人拆句的自检（v0.4 P0-b）。

分三层，从纯函数到真库迁移，跑完不留痕迹：

  1. 文本重建（join_words）—— 中文不能插空格，英文必须插
  2. 切分算法（split_segment / plan）—— 重叠投票、短片段合并、诚实降级、幂等
  3. 真库迁移（migrate）—— 在一个临时项目上真的建句库/评分/复习/流水/译文/字幕，
     拆句后逐项核对编号有没有丢，最后把临时项目整个删掉

判别点：**句库按自己的时间区间落位，评分/复习按「最长子句」落位**，
两者会落到不同的新编号。如果实现里两者用了同一个规则，这个测试会失败。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videonote import db                                    # noqa: E402
from videonote.config import settings                       # noqa: E402
from videonote.core import split                            # noqa: E402

PID = "p_selftest_split"
OK, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (OK if cond else FAIL).append(name)
    print(f"  {'✅' if cond else '❌'} {name}{('  ' + detail) if detail else ''}")


# --------------------------------------------------------------------------
# 1. 文本重建
# --------------------------------------------------------------------------
def test_join_words() -> None:
    print("\n[1] 文本重建 join_words")
    j = split.join_words
    check("中文不插空格", j([{"w": "你"}, {"w": "好"}]) == "你好", repr(j([{"w": "你"}, {"w": "好"}])))
    check("英文插空格", j([{"w": "hello"}, {"w": "world"}]) == "hello world")
    check("标点前不插空格", j([{"w": "hello"}, {"w": ","}, {"w": "world"}]) == "hello, world",
          repr(j([{"w": "hello"}, {"w": ","}, {"w": "world"}])))
    check("中文标点前不插空格", j([{"w": "好"}, {"w": "，"}, {"w": "对"}]) == "好，对")
    check("中英混排：英文后接中文不插", j([{"w": "OK"}, {"w": "好"}]) == "OK好")
    check("空词表返回空串", j([]) == "")
    check("空白词被跳过", j([{"w": " "}, {"w": "a"}]) == "a")


# --------------------------------------------------------------------------
# 2. 切分算法
# --------------------------------------------------------------------------
WIN = [
    {"start": 0.0, "end": 5.0, "label": "说话人1"},
    {"start": 5.0, "end": 8.0, "label": "说话人1"},
    {"start": 8.0, "end": 12.0, "label": "说话人2"},
    {"start": 12.0, "end": 16.0, "label": "说话人2"},
]

# 真实场景：_tidy() 把「A 说一句 + B 回一句」合成一句（5.0~12.0），
# 但每个词的时间戳还在，所以能切开。
MERGED = {
    "idx": 1, "start": 5.0, "end": 12.0, "text": "hello there how are you",
    "speaker": "说话人1",
    "words": [
        {"w": "hello", "start": 5.0, "end": 5.4},
        {"w": "there", "start": 5.4, "end": 5.9},
        {"w": "how", "start": 8.0, "end": 8.3},
        {"w": "are", "start": 8.3, "end": 8.6},
        {"w": "you", "start": 8.6, "end": 9.0},
    ],
}
SINGLE = {
    "idx": 0, "start": 0.0, "end": 5.0, "text": "你好", "speaker": "说话人1",
    "words": [{"w": "你", "start": 0.5, "end": 1.0}, {"w": "好", "start": 1.0, "end": 1.5}],
}


def test_split_segment() -> None:
    print("\n[2] 切分算法")
    check("重叠最长者胜出",
          split.speaker_at(WIN, 3.0, 4.0, "") == "说话人1"
          and split.speaker_at(WIN, 9.0, 10.0, "") == "说话人2",
          f"{split.speaker_at(WIN, 3.0, 4.0, '')} / {split.speaker_at(WIN, 9.0, 10.0, '')}")
    check("正好骑在窗口边界上（重叠打平）时取先出现的那个",
          split.speaker_at(WIN, 7.5, 8.5, "") == "说话人1")
    check("完全不重叠时用 fallback", split.speaker_at(WIN, 100.0, 101.0, "谁") == "谁")
    check("空窗口表不炸", split.speaker_at([], 0, 1, "x") == "x")

    parts = split.split_segment(MERGED, WIN)
    check("合成句被切成 2 段", len(parts) == 2, f"实际 {len(parts)}")
    if len(parts) == 2:
        check("第 1 段是说话人1", parts[0]["speaker"] == "说话人1")
        check("第 2 段是说话人2", parts[1]["speaker"] == "说话人2")
        check("第 1 段文本正确", parts[0]["text"] == "hello there", repr(parts[0]["text"]))
        check("第 2 段文本正确", parts[1]["text"] == "how are you", repr(parts[1]["text"]))
        check("边界按词收紧（不是整句边界）",
              parts[0]["start"] == 5.0 and parts[0]["end"] == 5.9
              and parts[1]["start"] == 8.0 and parts[1]["end"] == 9.0,
              f"{parts[0]['start']}-{parts[0]['end']} / {parts[1]['start']}-{parts[1]['end']}")
        check("词跟着各自的段走",
              [w["w"] for w in parts[0]["words"]] == ["hello", "there"]
              and [w["w"] for w in parts[1]["words"]] == ["how", "are", "you"])

    check("单说话人句子原样不动", len(split.split_segment(SINGLE, WIN)) == 1)
    check("没有词 -> 诚实降级（不猜）",
          len(split.split_segment({**MERGED, "words": []}, WIN)) == 1)
    check("没有窗口 -> 诚实降级",
          len(split.split_segment(MERGED, [])) == 1)

    # 幂等：拆过的句子再拆不该再变
    again = split.split_segment(parts[0], WIN) + split.split_segment(parts[1], WIN)
    check("已拆过的段再拆是幂等的", len(again) == 2, f"实际 {len(again)}")

    # 碎片合并：A B A 里 B 只有 0.3 秒 -> 被并进 A -> 于是相邻两组都是「说话人1」，
    # 必须再合并，否则句子会在一个**并不存在的说话人边界**上被切开
    win_aba = [
        {"start": 0.0, "end": 8.0, "label": "说话人1"},
        {"start": 8.0, "end": 9.05, "label": "说话人2"},
        {"start": 9.05, "end": 16.0, "label": "说话人1"},
    ]
    aba = {
        "idx": 9, "start": 1.0, "end": 12.0, "text": "a b c d", "speaker": "说话人1",
        "words": [
            {"w": "a", "start": 1.0, "end": 1.5},
            {"w": "b", "start": 1.5, "end": 6.0},
            {"w": "c", "start": 8.7, "end": 9.0},
            {"w": "d", "start": 9.5, "end": 12.0},
        ],
    }
    aba_parts = split.split_segment(aba, win_aba)
    check("A·B·A 里过短的 B 被并掉，不会切出同标签的假边界",
          len(aba_parts) == 1, f"实际 {len(aba_parts)}")
    if len(aba_parts) == 1:
        check("并组后词一个都没丢", aba_parts[0]["text"] == "a b c d", repr(aba_parts[0]["text"]))


def test_plan() -> None:
    print("\n[3] 整体计划 plan()")
    segs = [SINGLE, MERGED, {"idx": 2, "start": 12.0, "end": 16.0, "text": "bye",
                             "speaker": "说话人2",
                             "words": [{"w": "bye", "start": 12.5, "end": 13.0}]}]
    new_segs, mapping = split.plan(segs, WIN)
    check("新句子数 = 4", len(new_segs) == 4, f"实际 {len(new_segs)}")
    check("映射 = [(0,0,1),(1,1,2),(2,3,1)]", mapping == [(0, 0, 1), (1, 1, 2), (2, 3, 1)], str(mapping))
    check("changed_count = 1", split.changed_count(mapping) == 1)
    check("新编号连续 0..3", [s["idx"] for s in new_segs] == [0, 1, 2, 3])
    # 单射性：不同旧句的区间不重叠 —— clips/reviews 的 UNIQUE 约束靠这个才不冲突
    spans = [(a, a + n) for _, a, n in mapping]
    injective = all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
    check("旧->新映射在句子层面是单射（UNIQUE 不会冲突）", injective, str(spans))


# --------------------------------------------------------------------------
# 4. 真库迁移
# --------------------------------------------------------------------------
def _mk_project() -> None:
    _rm_project()
    db.create_project("自检：按说话人拆句", pid=PID)
    db.replace_segments(PID, [
        {"idx": 0, "start": 0.0, "end": 5.0, "text": "你好", "speaker": "说话人1",
         "words": [{"w": "你", "start": 0.5, "end": 1.0}, {"w": "好", "start": 1.0, "end": 1.5}]},
        MERGED,
        {"idx": 2, "start": 12.0, "end": 16.0, "text": "bye", "speaker": "说话人2",
         "words": [{"w": "bye", "start": 12.5, "end": 13.0}]},
    ])
    # 句库：时间区间故意偏向第 1 段（5.0~8.5），所以它该落到新 1 号，
    # 而评分/复习该落到新 2 号（更长的那段）—— 两条规则的结果必须不同。
    db.upsert_clip(PID, 1, start=5.0, end=8.5, text="hello there", translation="你好")
    db.add_score(PID, 1, score=55.0, accuracy=50.0, target="hello there")
    db.upsert_review(PID, 1, reason="自检", best_score=55.0, status="new")
    db.add_practice(PID, 1, kind="repeat", score=55.0)
    db.add_practice(PID, 2, kind="repeat", score=90.0)
    db.add_practice(PID, 0, kind="repeat", score=90.0)

    d = settings.project_dir(PID)
    (d / "seg_audio").mkdir(parents=True, exist_ok=True)
    (d / "seg_audio" / "seg_1.mp3").write_bytes(b"fake")
    (d / "artifacts" / "translation").mkdir(parents=True, exist_ok=True)
    tr = d / "artifacts" / "translation" / "translation.json"
    tr.write_text(json.dumps({"0": "T0", "1": "T1", "2": "T2"}, ensure_ascii=False), encoding="utf-8")
    db.put_artifact(PID, "translation", str(tr), kind="json")
    (d / "artifacts" / "transcript").mkdir(parents=True, exist_ok=True)
    tj = d / "artifacts" / "transcript" / "transcript.json"
    tj.write_text(json.dumps({"language": "en", "segments": [], "model": "t"}, ensure_ascii=False),
                  encoding="utf-8")
    db.put_artifact(PID, "transcript", str(tj), kind="json")
    (d / "artifacts" / "subtitles").mkdir(parents=True, exist_ok=True)
    srt = d / "artifacts" / "subtitles" / "subtitle.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\n你好\n", encoding="utf-8")
    db.put_artifact(PID, "subtitles_srt", str(srt), kind="subtitle")


def _rm_project() -> None:
    db.delete_project(PID)
    db.cleanup_orphans()
    try:
        shutil.rmtree(settings.project_dir(PID), ignore_errors=True)
    except OSError:
        pass


def test_transaction_rollback() -> None:
    print("\n[4] 事务原子性（迁移的保命机制）")
    before = len(db.list_practice(PID))
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO practice_log (project_id,seg_idx,kind,score,seconds,day,created_at)"
                " VALUES (?,?,?,?,?,?,?)", (PID, 999, "rollback-test", 0, 0, "1970-01-01", "1970-01-01"))
            raise RuntimeError("故意失败")
    except RuntimeError:
        pass
    after = len(db.list_practice(PID))
    check("事务失败后整体回滚（没有半成品）", before == after, f"{before} -> {after}")


def test_migrate() -> None:
    print("\n[5] 真库迁移 migrate()")
    segs = db.list_segments(PID)
    new_segs, mapping = split.plan(segs, WIN)
    check("3 句 -> 4 句", len(new_segs) == 4, f"实际 {len(new_segs)}")

    moved = split.migrate(PID, new_segs, mapping, backup=False)

    check("segments 表已重写为 4 句", len(db.list_segments(PID)) == 4)
    check("新句子说话人正确",
          [s["speaker"] for s in db.list_segments(PID)] == ["说话人1", "说话人1", "说话人2", "说话人2"],
          str([s["speaker"] for s in db.list_segments(PID)]))

    clips = db.list_clips(PID)
    check("句库落位=按自身时间区间重叠（新 1 号）",
          len(clips) == 1 and int(clips[0]["seg_idx"]) == 1,
          f"实际 {[c['seg_idx'] for c in clips]}")
    check("句库自己的 start/end/文本没被动过",
          clips and clips[0]["start"] == 5.0 and clips[0]["end"] == 8.5 and clips[0]["text"] == "hello there")

    scores = db.list_scores(PID, limit=50)
    check("评分落位=最长子句（新 2 号）",
          len(scores) == 1 and int(scores[0]["seg_idx"]) == 2,
          f"实际 {[s['seg_idx'] for s in scores]}")
    check("评分分数没丢", scores and float(scores[0]["score"]) == 55.0)

    revs = db.list_reviews(pid=PID)
    check("复习队列落位=最长子句（新 2 号）",
          len(revs) == 1 and int(revs[0]["seg_idx"]) == 2,
          f"实际 {[r['seg_idx'] for r in revs]}")

    prac = {int(p["seg_idx"]) for p in db.list_practice(pid=PID)}
    check("流水全部迁移（0 / 2 / 3 号）", prac == {0, 2, 3}, str(sorted(prac)))

    check("句库与评分落到了不同编号（证明两条规则真的分开）",
          int(clips[0]["seg_idx"]) != int(scores[0]["seg_idx"]))

    # 译文
    art = db.get_artifact(PID, "translation")
    tr = json.loads(Path(art["path"]).read_text(encoding="utf-8")) if art else {}
    check("译文按最长子句归属", tr == {"0": "T0", "2": "T1", "3": "T2"}, str(tr))
    check("译文 artifact 标了 split_stale", bool((art or {}).get("meta", {}).get("split_stale")))

    # transcript 重写
    tart = db.get_artifact(PID, "transcript")
    tdata = json.loads(Path(tart["path"]).read_text(encoding="utf-8")) if tart else {}
    check("transcript.json 按新句子重写",
          len(tdata.get("segments") or []) == 4
          and [s["idx"] for s in tdata["segments"]] == [0, 1, 2, 3],
          f"实际 {len(tdata.get('segments') or [])} 段")

    # 缓存清理
    check("seg_audio 缓存被清空（否则「听原声」会放别人的声音）",
          not (settings.project_dir(PID) / "seg_audio").exists())

    # 作废产出
    check("字幕产出被作废（需要重新生成）",
          db.get_artifact(PID, "subtitles_srt") is None)

    # 计数器语义 =「真的改了号的行数」。这个 clip 的正确落位恰好还是 1 号，
    # 所以它没有被改号（但它指向的句子已经从整句变成只有前半句了）。
    check("迁移计数只算真的改了号的行",
          moved.get("split") == 1 and moved.get("clips") == 0 and moved.get("scores") == 1
          and moved.get("reviews") == 1 and moved.get("practice") == 2,
          str({k: moved.get(k) for k in ("split", "clips", "scores", "reviews", "practice")}))
    check("句库确实按『自身区间』落位（同一句里两种区间给出不同目标）",
          split._pick_overlap(new_segs, 1, 2, 5.0, 8.5) == 1
          and split._pick_overlap(new_segs, 1, 2, 8.0, 12.0) == 2)

    # 再跑一次必须无事发生（幂等）
    new2, map2 = split.plan(db.list_segments(PID), WIN)
    check("二次拆句不再改动（幂等）", split.changed_count(map2) == 0, f"changed={split.changed_count(map2)}")


# --------------------------------------------------------------------------
def main() -> int:
    print("=" * 72)
    print("按说话人拆句自检（v0.4 P0-b）—— 跑完不留痕迹")
    print("=" * 72)
    _mk_project()
    try:
        test_join_words()
        test_split_segment()
        test_plan()
        test_transaction_rollback()
        test_migrate()
    finally:
        _rm_project()

    print("\n" + "=" * 72)
    print(f"通过 {len(OK)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
