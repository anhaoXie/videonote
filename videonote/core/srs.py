# -*- coding: utf-8 -*-
"""间隔重复复习队列（SM-2 简化版）。

核心主张：练口语最大的敌人不是「不会」，是「忘了哪句没练好」。
所以：

  * 你只要在跟读评分里读错了一句（低于 80 分），它**自动**进复习队列；
  * 你收藏的句子也算「想练的」，一起进队列；
  * 之后按 SM-2 的节奏重新出现在你面前：今天 → 明天 → 3 天 → 7 天 → 半个月 …
  * 每次复习的分数会重新决定下一次什么时候来：读得好就往后推，读砸了当天再来。
  * **单词课也是同一套卡**：最小对立对里练砸的词（ship/sheep 里的 ship）同样进这个队列。
    句子卡和单词卡存在两张表里（身份约束不同：句子是 (project_id, seg_idx)，单词两者都没有），
    但「今天练什么」是同一个视图 —— 见 unified_queue。

这样你打开「复习」页永远只需要做一件事：把今天的卡过完。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .. import db
from . import minimal_pairs as MP

REVIEW_THRESHOLD = 80.0        # 低于这个分数就进队列
EASY_SCORE = 92.0              # 达到这个分数算「掌握得不错」，间隔往后推
LEARNING_STEP_MINUTES = 10     # 学习阶段没读好，10 分钟后再来
MIN_EASE = 1.3
MAX_INTERVAL_DAYS = 365.0
DEFAULT_LIMIT = 40

# --- 单词卡 -----------------------------------------------------------------
KEY_PHONE_MISS_CAP = 40.0      # 关键音没读对时的分数上限（保证掉回学习阶段）
LISTEN_CORRECT_SCORE = 95.0    # 听音辨词选对
LISTEN_WRONG_SCORE = 30.0      # 听音辨词选错：连听都分不开，比读错更该练
WORD_RATIO = 1.0 / 3.0         # 混合队列里单词卡的目标占比（约每 2 句夹 1 个词）

ISO = "%Y-%m-%dT%H:%M:%S"


# --------------------------------------------------------------------------
# 时间小工具
# --------------------------------------------------------------------------
def _now(when: Optional[datetime] = None) -> datetime:
    return when or datetime.now()


def _iso(dt: datetime) -> str:
    return dt.strftime(ISO)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    for fmt in (ISO, "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# 打分 -> 记忆质量（SM-2 的 q，0~5）
# --------------------------------------------------------------------------
def score_to_quality(score: float) -> int:
    s = float(score or 0)
    if s >= 92:
        return 5
    if s >= 80:
        return 4
    if s >= 65:
        return 3
    if s >= 45:
        return 2
    if s > 0:
        return 1
    return 0


def schedule(row: Optional[dict], score: float, when: Optional[datetime] = None) -> Dict[str, Any]:
    """根据这次的分数算下一次该什么时候复习。返回可直接写库的字段。"""
    now = _now(when)
    row = row or {}
    quality = score_to_quality(score)
    ease = float(row.get("ease") or 2.5)
    reps = int(row.get("reps") or 0)
    lapses = int(row.get("lapses") or 0)
    interval = float(row.get("interval_days") or 0)
    best = max(float(row.get("best_score") or 0), float(score or 0))

    if quality < 3:
        # 没读好：打回学习阶段，当天/十分钟后再来
        reps = 0
        lapses += 1
        interval = 0.0
        due = now + timedelta(minutes=LEARNING_STEP_MINUTES)
        status = "learning"
    else:
        reps += 1
        if reps == 1:
            interval = 1.0
        elif reps == 2:
            interval = 3.0
        else:
            interval = min(MAX_INTERVAL_DAYS, max(1.0, interval * ease))
        # SM-2 的难度调整
        ease = max(MIN_EASE, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        due = now + timedelta(days=interval)
        status = "review"

    return {
        "ease": round(ease, 3),
        "interval_days": round(interval, 3),
        "reps": reps,
        "lapses": lapses,
        "due_at": _iso(due),
        "last_at": _iso(now),
        "last_score": round(float(score or 0), 1),
        "best_score": round(best, 1),
        "status": status,
    }


def is_due(row: dict, when: Optional[datetime] = None) -> bool:
    if (row.get("status") or "") == "suspended":
        return False
    due = _parse(row.get("due_at"))
    if due is None:
        return True
    return due <= _now(when)


# --------------------------------------------------------------------------
# 把「该复习的句子」找出来
# --------------------------------------------------------------------------
def _translation_map(pid: str) -> Dict[str, str]:
    art = db.get_artifact(pid, "translation")
    if not art or not art.get("path"):
        return {}
    p = Path(art["path"])
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def sync(pid: Optional[str] = None) -> Dict[str, Any]:
    """扫评分记录和句库，把该复习的句子补进队列。幂等，随时可调。"""
    projects = [db.get_project(pid)] if pid else db.list_projects()
    created = 0
    scanned = 0
    for project in projects:
        if not project:
            continue
        pid_ = project["id"]
        existing = {int(r["seg_idx"]) for r in db.list_reviews(pid_)}
        best = db.best_scores(pid_)
        for seg_idx, st in best.items():
            scanned += 1
            if seg_idx in existing:
                continue
            top = float(st.get("best") or 0)
            if top < REVIEW_THRESHOLD:
                db.upsert_review(pid_, int(seg_idx), reason=f"最高只拿到 {top:.0f} 分",
                                 last_score=top, best_score=top, status="new",
                                 due_at=_iso(datetime.now()))
                existing.add(int(seg_idx))
                created += 1
        for clip in db.list_clips(pid_):
            seg_idx = int(clip["seg_idx"])
            scanned += 1
            if seg_idx in existing:
                continue
            db.upsert_review(pid_, seg_idx, reason="你收藏了这句",
                             best_score=float((best.get(seg_idx) or {}).get("best") or 0),
                             status="new", due_at=_iso(datetime.now()))
            existing.add(seg_idx)
            created += 1
    return {"created": created, "scanned": scanned}


def _enrich(rows: List[dict]) -> List[dict]:
    """给队列项补上句子文本、译文、项目名、音频地址。"""
    by_project: Dict[str, Dict[str, Any]] = {}
    out: List[dict] = []
    for row in rows:
        pid = row["project_id"]
        if pid not in by_project:
            project = db.get_project(pid) or {}
            segs = {int(s["idx"]): s for s in db.list_segments(pid)}
            clips = {int(c["seg_idx"]): c for c in db.list_clips(pid)}
            by_project[pid] = {
                "title": project.get("title") or pid,
                "segs": segs,
                "clips": clips,
                "tr": _translation_map(pid),
            }
        ctx = by_project[pid]
        seg = ctx["segs"].get(int(row["seg_idx"])) or {}
        clip = ctx["clips"].get(int(row["seg_idx"])) or {}
        item = dict(row)
        item.update({
            "project_title": ctx["title"],
            "text": seg.get("text") or clip.get("text") or "",
            "translation": clip.get("translation") or ctx["tr"].get(str(row["seg_idx"]), ""),
            "start": float(seg.get("start") or clip.get("start") or 0),
            "end": float(seg.get("end") or clip.get("end") or 0),
            "speaker": seg.get("speaker") or "",
            "clip_id": clip.get("id"),
            "audio_url": (f"/api/clips/{clip['id']}/media?kind=audio" if clip.get("id")
                          else f"/api/projects/{pid}/segments/{row['seg_idx']}/audio"),
            "due": is_due(row),
        })
        out.append(item)
    return out


def due_queue(pid: Optional[str] = None, limit: int = DEFAULT_LIMIT, when: Optional[datetime] = None,
              include_future: bool = False) -> List[dict]:
    """今天该练的句子。include_future=True 时把还没到期的也带出来（用于预览）。"""
    now = _now(when)
    if include_future:
        rows = db.list_reviews(pid=pid, limit=500)
    else:
        rows = db.list_reviews(pid=pid, due_before=_iso(now), limit=500)
    rows = [r for r in rows if is_due(r, now)]
    # 先来的先练：逾期越久越靠前
    rows.sort(key=lambda r: (r.get("due_at") or "", -int(r.get("lapses") or 0)))
    cards = _enrich(rows[:limit])
    # 统一契约：从本模块出来的每张卡都带 card_type，
    # 这样调用方（前端 / unified_queue）不用猜这是句子卡还是单词卡。
    for c in cards:
        c.setdefault("card_type", "sentence")
    return cards


def counts(pid: Optional[str] = None, when: Optional[datetime] = None) -> Dict[str, Any]:
    now = _now(when)
    rows = db.list_reviews(pid=pid, limit=5000)
    due = [r for r in rows if is_due(r, now)]
    today = now.strftime("%Y-%m-%d")
    return {
        "total": len(rows),
        "due": len(due),
        "learning": sum(1 for r in rows if r.get("status") == "learning"),
        "reviewing": sum(1 for r in rows if r.get("status") == "review"),
        "new": sum(1 for r in rows if r.get("status") == "new"),
        "suspended": sum(1 for r in rows if r.get("status") == "suspended"),
        "overdue": sum(1 for r in rows if _parse(r.get("due_at")) and _parse(r["due_at"]).strftime("%Y-%m-%d") < today),
        "done_today": sum(1 for r in rows if (r.get("last_at") or "").startswith(today)),
    }


def forecast(pid: Optional[str] = None, days: int = 30, when: Optional[datetime] = None) -> List[dict]:
    """未来每天的复习量（统计页用来画柱子）。"""
    now = _now(when)
    buckets: Dict[str, int] = {}
    for r in db.list_reviews(pid=pid, limit=5000):
        if (r.get("status") or "") == "suspended":
            continue
        due = _parse(r.get("due_at")) or now
        day = due.strftime("%Y-%m-%d")
        if (due - now).days > days:
            continue
        key = day if day >= now.strftime("%Y-%m-%d") else now.strftime("%Y-%m-%d")
        buckets[key] = buckets.get(key, 0) + 1
    return [{"day": now.strftime("%Y-%m-%d") if i == 0 else (now + timedelta(days=i)).strftime("%Y-%m-%d"),
             "count": buckets.get((now + timedelta(days=i)).strftime("%Y-%m-%d"), 0)}
            for i in range(days)]


# --------------------------------------------------------------------------
# 复习结果写回
# --------------------------------------------------------------------------
def grade(rid: int, score: float, *, seconds: float = 0, kind: str = "review") -> Dict[str, Any]:
    row = db.get_review(rid)
    if not row:
        raise KeyError(f"复习项 {rid} 不存在")
    fields = schedule(row, score)
    db.update_review(rid, **fields)
    db.add_practice(row["project_id"], seg_idx=int(row["seg_idx"]), kind=kind,
                    score=float(score or 0), seconds=float(seconds or 0))
    return {"review": db.get_review(rid), **fields}


def note_score(pid: str, seg_idx: int, score: float, *, seconds: float = 0,
               mode: str = "repeat") -> Dict[str, Any]:
    """评分接口的统一入口：记一次训练流水，并按需进队列/推进调度。"""
    db.add_practice(pid, seg_idx=int(seg_idx), kind=mode, score=float(score or 0),
                    seconds=float(seconds or 0))
    row = db.query_one("SELECT * FROM reviews WHERE project_id=? AND seg_idx=?", (pid, int(seg_idx)))
    if row:
        fields = schedule(row, score)
        db.update_review(row["id"], **fields)
        return {"review_id": row["id"], "next_due": fields["due_at"], "interval_days": fields["interval_days"],
                "status": fields["status"], "graduated": fields["status"] == "review"}
    if float(score or 0) < REVIEW_THRESHOLD:
        created = db.upsert_review(pid, int(seg_idx), reason=f"跟读只拿到 {score:.0f} 分",
                                   last_score=float(score or 0), best_score=float(score or 0),
                                   status="new", due_at=_iso(datetime.now()))
        return {"review_id": created["id"], "next_due": created["due_at"], "interval_days": 0,
                "status": "new", "queued": True}
    return {}


def snooze(rid: int, days: float = 1) -> dict:
    row = db.get_review(rid)
    if not row:
        raise KeyError(f"复习项 {rid} 不存在")
    due = _iso(datetime.now() + timedelta(days=float(days)))
    db.update_review(rid, due_at=due)
    return {"id": rid, "due_at": due}


def suspend(rid: int, suspended: bool = True) -> dict:
    db.update_review(rid, status="suspended" if suspended else "new",
                     due_at=_iso(datetime.now()))
    return db.get_review(rid) or {}


def reset(rid: int) -> dict:
    row = db.get_review(rid)
    if not row:
        raise KeyError(f"复习项 {rid} 不存在")
    db.update_review(rid, ease=2.5, interval_days=0, reps=0, lapses=0, status="new",
                     due_at=_iso(datetime.now()))
    return db.get_review(rid) or {}


def add_manual(pid: str, seg_idx: int, reason: str = "手动加入复习") -> dict:
    row = db.upsert_review(pid, int(seg_idx), reason=reason, status="new",
                           due_at=_iso(datetime.now()))
    return row


# 对外公开的名字（内部实现叫 _enrich）
enrich = _enrich


# ==========================================================================
# 单词卡（最小对立对）—— 跟句子共用一套 SM-2 调度，但存在自己的表里
# ==========================================================================
def score_word_drill(accuracy: float, key_hit: Optional[bool]) -> float:
    """把「跟读一个词」的结果换算成复习分。

    为什么要单独算：整词 accuracy 会被其它音掩盖 —— think 读成 sink 也有约 50%。
    只看 accuracy 的话，读错关键音的词永远进不了队列，单词课就白练了。
    """
    acc = max(0.0, min(100.0, float(accuracy or 0)))
    if key_hit is False:
        return round(min(acc, KEY_PHONE_MISS_CAP), 1)
    return round(acc, 1)


def score_listen_drill(correct: bool) -> float:
    """听音辨词的复习分：连听都分不开，比读错更该练。"""
    return LISTEN_CORRECT_SCORE if correct else LISTEN_WRONG_SCORE


def _word_why(pair: dict, word: str, score: float, key_hit: Optional[bool], mode: str) -> str:
    a, b = str(pair.get("a") or ""), str(pair.get("b") or "")
    if mode == "listen":
        return f"听音辨词没分清 {a} / {b}"
    if key_hit is False:
        return f"跟读 {word} 时关键音 /{pair.get('pa') or pair.get('pb') or '?'}/ 没过关"
    return f"跟读 {word} 只拿到 {float(score or 0):.0f} 分"


def word_note(pair_id: str, word: str, score: float, *, key_hit: Optional[bool] = None,
              mode: str = "say", seconds: float = 0.0) -> Dict[str, Any]:
    """单词训练的统一调度入口：已有卡就推进，没卡且练砸了才建卡。

    语义跟 note_score 完全对齐 —— 一次就练好的东西不该让队列变长。
    """
    pair = MP.find(pair_id) or {}
    word = str(word or "").strip().lower()
    if not word:
        return {}
    score = float(score or 0)
    row = db.find_word_review(pair_id, word)
    if row:
        fields = schedule(row, score)
        db.update_word_review(row["id"], **fields)
        return {"word_review_id": row["id"], "next_due": fields["due_at"],
                "interval_days": fields["interval_days"], "status": fields["status"],
                "graduated": fields["status"] == "review", "queued": False}
    if score < REVIEW_THRESHOLD:
        created = db.upsert_word_review(
            pair_id, word, reason=_word_why(pair, word, score, key_hit, mode),
            last_score=score, best_score=score, status="new", due_at=_iso(_now()))
        return {"word_review_id": created["id"], "next_due": created["due_at"],
                "interval_days": 0, "status": "new", "queued": True}
    return {}


def word_grade(wrid: int, score: float, *, seconds: float = 0.0, kind: str = "review") -> Dict[str, Any]:
    """给单词卡打分（复习页用）。"""
    row = db.get_word_review(wrid)
    if not row:
        raise KeyError(f"单词卡 {wrid} 不存在")
    fields = schedule(row, float(score or 0))
    db.update_word_review(wrid, **fields)
    db.add_pair_drill(row["pair_id"], word=row["word"], kind=kind,
                      correct=float(score or 0) >= REVIEW_THRESHOLD,
                      score=float(score or 0),
                      detail={"seconds": float(seconds or 0), "from": "review"})
    return {"word_review": db.get_word_review(wrid), **fields}


def _word_enrich(rows: List[dict]) -> List[dict]:
    """给单词卡补上词对信息、该练哪个音、音频地址。"""
    out: List[dict] = []
    for row in rows:
        pair = MP.find(row["pair_id"]) or {}
        word = str(row["word"] or "").strip().lower()
        a = str(pair.get("a") or "").strip().lower()
        b = str(pair.get("b") or "").strip().lower()
        side = "a" if word == a else ("b" if word == b else "")
        key_phone = (pair.get("pa") if side == "a" else pair.get("pb") if side == "b" else "") or ""
        other = (pair.get("b") if side == "a" else pair.get("a")) or ""
        item = dict(row)
        item.update({
            "card_type": "word",
            "word": word,
            "text": word,                     # 让前端能跟句子卡共用一套渲染
            "pair_id": row["pair_id"],
            "pair": {"a": pair.get("a") or "", "b": pair.get("b") or "",
                     "pa": pair.get("pa") or "", "pb": pair.get("pb") or ""},
            "group": pair.get("group") or "",
            "side": side,
            "other": other,
            "key_phone": key_phone,
            "tip": pair.get("tip") or "",
            "audio_url": f"/api/words/audio?word={quote(word)}",
            "other_audio_url": f"/api/words/audio?word={quote(other)}" if other else "",
            "due": is_due(row),
        })
        out.append(item)
    return out


def word_due_queue(limit: int = DEFAULT_LIMIT, when: Optional[datetime] = None,
                   include_future: bool = False) -> List[dict]:
    now = _now(when)
    if include_future:
        rows = db.list_word_reviews(limit=500)
    else:
        rows = db.list_word_reviews(due_before=_iso(now), limit=500)
    rows = [r for r in rows if is_due(r, now)]
    rows.sort(key=lambda r: (r.get("due_at") or "", -int(r.get("lapses") or 0)))
    return _word_enrich(rows[:limit])


def word_counts(when: Optional[datetime] = None) -> Dict[str, Any]:
    now = _now(when)
    rows = db.list_word_reviews(limit=5000)
    due = [r for r in rows if is_due(r, now)]
    today = now.strftime("%Y-%m-%d")
    return {
        "total": len(rows),
        "due": len(due),
        "learning": sum(1 for r in rows if r.get("status") == "learning"),
        "reviewing": sum(1 for r in rows if r.get("status") == "review"),
        "new": sum(1 for r in rows if r.get("status") == "new"),
        "suspended": sum(1 for r in rows if r.get("status") == "suspended"),
        "overdue": sum(1 for r in rows
                       if _parse(r.get("due_at")) and _parse(r["due_at"]).strftime("%Y-%m-%d") < today),
        "done_today": sum(1 for r in rows if (r.get("last_at") or "").startswith(today)),
    }


def word_sync() -> Dict[str, Any]:
    """扫单词练习流水，把「练砸过的词」补进队列。幂等，随时可调。"""
    created = 0
    scanned = 0
    for st in db.word_drill_stats(limit=20000):
        scanned += 1
        pair_id = st["pair_id"]
        word = str(st["word"] or "").strip().lower()
        if not word or db.find_word_review(pair_id, word):
            continue
        best = float(st.get("best") or 0)
        n = int(st.get("n") or 0)
        # 听音辨词一次都没对，或跟读最高分都没到线 -> 这个词该练
        bad = (st.get("kind") == "listen" and int(st.get("ok") or 0) == 0) or best < REVIEW_THRESHOLD
        if not bad:
            continue
        db.upsert_word_review(pair_id, word, reason=f"历史练习最高 {best:.0f} 分（{n} 次）",
                              last_score=best, best_score=best, status="new",
                              due_at=_iso(_now()))
        created += 1
    return {"created": created, "scanned": scanned}


def word_snooze(wrid: int, days: float = 1) -> dict:
    row = db.get_word_review(wrid)
    if not row:
        raise KeyError(f"单词卡 {wrid} 不存在")
    due = _iso(_now() + timedelta(days=float(days)))
    db.update_word_review(wrid, due_at=due)
    return {"id": wrid, "due_at": due}


def word_suspend(wrid: int, suspended: bool = True) -> dict:
    db.update_word_review(wrid, status="suspended" if suspended else "new", due_at=_iso(_now()))
    return db.get_word_review(wrid) or {}


def word_add(pair_id: str, word: str, reason: str = "手动加入复习") -> dict:
    return db.upsert_word_review(pair_id, word, reason=reason, status="new", due_at=_iso(_now()))


# --------------------------------------------------------------------------
# 统一今日队列：句子卡 + 单词卡
# --------------------------------------------------------------------------
def _interleave(sentences: List[dict], words: List[dict], word_ratio: float) -> List[dict]:
    """按比率交错两串已排好序的卡（各自内部顺序不变）。

    不简单拼接的原因：单词卡一次几秒，拼在最前后都会堆成一坨 ——
    交错才是「热身 → 主练 → 换脑子」的真实节奏。
    """
    # 注意不能用 `word_ratio or WORD_RATIO`：0.0 是「不要单词卡」的合法值，不是「没传」
    ratio = float(WORD_RATIO if word_ratio is None else word_ratio)
    if ratio <= 0 or not words:
        return list(sentences)
    ratio = max(0.05, min(0.9, ratio))
    per_word = max(1, int(round((1 - ratio) / ratio)))   # 每张词卡前放几张句子卡
    out: List[dict] = []
    si = wi = 0
    while si < len(sentences) or wi < len(words):
        for _ in range(per_word):
            if si < len(sentences):
                out.append(sentences[si])
                si += 1
        if wi < len(words):
            out.append(words[wi])
            wi += 1
    return out


def unified_queue(pid: Optional[str] = None, limit: int = DEFAULT_LIMIT,
                  when: Optional[datetime] = None, include_future: bool = False,
                  include_words: bool = True, word_ratio: float = WORD_RATIO) -> List[dict]:
    """「今天练什么」的合并视图。

    不做成一张表：句子卡的身份是 (project_id, seg_idx)，单词卡两者都没有。
    统一只发生在「读的时候」，写的时候各回各家 —— 这样谁都不用放宽唯一约束。
    """
    sentences = due_queue(pid=pid, limit=limit, when=when, include_future=include_future)
    for it in sentences:
        it.setdefault("card_type", "sentence")
    if not include_words:
        return sentences
    # 单词卡不绑项目：练的是你自己的发音，所以当前项目视图里也一并给
    ratio = max(0.0, min(0.9, float(WORD_RATIO if word_ratio is None else word_ratio)))
    if ratio <= 0:
        return sentences
    want = max(1, int(round(limit * ratio)))
    words = word_due_queue(limit=want, when=when, include_future=include_future)
    if not words:
        return sentences
    return _interleave(sentences, words, word_ratio)[:limit]


def unified_counts(pid: Optional[str] = None, when: Optional[datetime] = None) -> Dict[str, Any]:
    """合并计数：due/total/done_today 是两者之和，原句子字段保留不变。"""
    s = counts(pid, when)
    w = word_counts(when)
    return {
        **s,
        "words": w,
        "due_sentences": s.get("due") or 0,
        "due_words": w.get("due") or 0,
        "due": (s.get("due") or 0) + (w.get("due") or 0),
        "total": (s.get("total") or 0) + (w.get("total") or 0),
        "done_today": (s.get("done_today") or 0) + (w.get("done_today") or 0),
    }


# 对外公开的名字（内部实现叫 _word_enrich）
word_enrich = _word_enrich
