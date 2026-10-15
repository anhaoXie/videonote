# -*- coding: utf-8 -*-
"""训练统计：把散落在 scores / practice_log / reviews 里的数据变成「你该知道的事」。

统计面板要回答的问题只有四个：
  1. 我到底练了多少？（次数 / 时长 / 连续天数）
  2. 我在变好吗？（分数趋势）
  3. 我最该补什么？（高频漏词、高频读错、最差的句子）
  4. 接下来要练什么？（复习队列的负载）

所以这里不追求图表花哨，只保证每个数字都能对应到一个「下一步动作」。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .. import db
from . import minimal_pairs as MP, srs

SCORE_COLS = ("id", "project_id", "seg_idx", "score", "accuracy", "fluency", "completeness",
              "detail", "mode", "created_at")


# --------------------------------------------------------------------------
def _all_scores(pid: Optional[str] = None, days: int = 0, limit: int = 50000) -> List[dict]:
    sql = f"SELECT {','.join(SCORE_COLS)} FROM scores WHERE 1=1"
    params: List[Any] = []
    if pid:
        sql += " AND project_id=?"
        params.append(pid)
    if days > 0:
        sql += " AND created_at>=?"
        params.append((date.today() - timedelta(days=days)).isoformat())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    for r in rows:
        r["detail"] = db.loads(r.get("detail"), {})
    return rows


def _day(ts: str) -> str:
    return (ts or "")[:10]


def _avg(vals: List[float]) -> float:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


# --------------------------------------------------------------------------
def overview(scores: List[dict], practice: List[dict], pid: Optional[str] = None,
             drills: Optional[List[dict]] = None) -> Dict[str, Any]:
    drills = drills or []
    if not scores and not practice and not drills:
        return {"attempts": 0, "sentences": 0, "avg_score": 0.0, "best_score": 0.0,
                "seconds": 0, "minutes": 0, "active_days": 0, "streak": 0, "longest_streak": 0,
                "first_at": "", "last_at": "", "recent_avg": 0.0, "delta": None,
                "word_attempts": 0, "word_ok_rate": 0.0}

    attempts = (len(scores) if scores else len(practice)) + len(drills)
    best = max((float(s["score"] or 0) for s in scores), default=0.0)
    seconds = 0.0
    for s in scores:
        seconds += float((s.get("detail") or {}).get("duration") or 0)
    # 影子跟读也计时长，但 detail.duration 是整段录音
    uniq = {(s["project_id"], int(s["seg_idx"])) for s in scores}
    days = sorted({_day(s["created_at"]) for s in scores if s.get("created_at")})
    if practice:
        days = sorted(set(days) | {p.get("day") or _day(p.get("created_at")) for p in practice})
    if drills:
        days = sorted((set(days) | {_day(d.get("created_at")) or d.get("day") or "" for d in drills})
                      - {""})

    # 连续天数：从今天（或昨天，防止今天还没练就断签）往回数
    streak = 0
    longest = 0
    if days:
        dset = set(days)
        cur = date.today()
        if cur.isoformat() not in dset:
            cur = cur - timedelta(days=1)
        while cur.isoformat() in dset:
            streak += 1
            cur -= timedelta(days=1)
        run = 0
        prev = None
        for d in days:
            try:
                dd = date.fromisoformat(d)
            except ValueError:
                continue
            run = run + 1 if (prev and (dd - prev).days == 1) else 1
            longest = max(longest, run)
            prev = dd

    ordered = sorted(scores, key=lambda s: s.get("created_at") or "")
    recent = [float(s["score"] or 0) for s in ordered[-20:]]
    earlier = [float(s["score"] or 0) for s in ordered[-40:-20]]
    delta = round(_avg(recent) - _avg(earlier), 1) if len(ordered) >= 20 else None

    return {
        "attempts": attempts,
        "sentences": len(uniq),
        "avg_score": _avg([float(s["score"] or 0) for s in scores]),
        "best_score": round(best, 1),
        "recent_avg": _avg(recent),
        "delta": delta,
        "seconds": round(seconds, 1),
        "minutes": round(seconds / 60, 1),
        "active_days": len(days),
        "streak": streak,
        "longest_streak": longest,
        "first_at": ordered[0]["created_at"] if ordered else "",
        "last_at": ordered[-1]["created_at"] if ordered else "",
        "word_attempts": len(drills),
        "word_ok_rate": (round(100.0 * sum(1 for d in drills if d.get("correct")) / len(drills), 1)
                         if drills else 0.0),
    }


def daily(scores: List[dict], practice: List[dict], days: int = 120,
          drills: Optional[List[dict]] = None) -> List[dict]:
    """最近 N 天每天的训练量 + 平均分（统计页的热力图/折线图数据）。

    单词练习单独记在 words 里 —— 它的分数尺度跟句子准确率不是一回事，混进平均分会说谎。
    """
    drills = drills or []
    today = date.today()
    buckets: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        buckets[d] = {"day": d, "attempts": 0, "score_sum": 0.0, "seconds": 0.0, "words": 0}
    for s in scores:
        d = _day(s.get("created_at"))
        if d in buckets:
            buckets[d]["attempts"] += 1
            buckets[d]["score_sum"] += float(s["score"] or 0)
            buckets[d]["seconds"] += float((s.get("detail") or {}).get("duration") or 0)
    for p in practice:
        d = p.get("day") or _day(p.get("created_at"))
        if d in buckets and not scores:
            buckets[d]["attempts"] += 1
            buckets[d]["seconds"] += float(p.get("seconds") or 0)
    for w in drills:
        d = _day(w.get("created_at")) or w.get("day") or ""
        if d in buckets:
            buckets[d]["words"] += 1
    out = []
    for d in sorted(buckets):
        b = buckets[d]
        out.append({"day": d, "attempts": b["attempts"],
                    "avg_score": round(b["score_sum"] / b["attempts"], 1) if b["attempts"] else 0.0,
                    "seconds": round(b["seconds"], 1), "words": b["words"]})
    return out


def distribution(scores: List[dict]) -> List[dict]:
    edges = [(0, 60, "还需努力"), (60, 70, "及格线"), (70, 80, "不错"), (80, 90, "挺好"), (90, 101, "几乎满分")]
    out = []
    for lo, hi, label in edges:
        n = sum(1 for s in scores if lo <= float(s["score"] or 0) < hi)
        out.append({"label": label, "lo": lo, "hi": hi if hi < 101 else 100, "count": n})
    return out


def weak_words(scores: List[dict], limit: int = 20) -> Dict[str, List[dict]]:
    """高频漏词 + 高频读错（含「读成了什么」）。"""
    missing: Dict[str, int] = {}
    wrong: Dict[tuple, int] = {}
    for s in scores:
        for w in (s.get("detail") or {}).get("words") or []:
            status = w.get("status")
            tok = (w.get("w") or "").strip().lower()
            if not tok:
                continue
            if status == "missing":
                missing[tok] = missing.get(tok, 0) + 1
            elif status == "wrong":
                heard = (w.get("heard") or "").strip().lower()
                wrong[(tok, heard)] = wrong.get((tok, heard), 0) + 1
    miss_rows = [{"word": k, "count": v} for k, v in sorted(missing.items(), key=lambda kv: -kv[1])][:limit]
    wrong_rows = [{"word": k[0], "heard": k[1], "count": v}
                  for k, v in sorted(wrong.items(), key=lambda kv: -kv[1])][:limit]
    return {"missing": miss_rows, "wrong": wrong_rows}


def weak_phonemes(scores: List[dict], limit: int = 20) -> List[dict]:
    """汇总音素级错误（音素评测开启后才有）。

    只统计「合法英文音素」——中文视频里老师随手念的字母（k、n）也被记进了历史明细，
    不过滤的话统计页会被这种噪声占满。
    """
    try:
        from .phonemes import EN_PHONES
    except Exception:  # noqa: BLE001
        EN_PHONES = frozenset()
    tally: Dict[tuple, int] = {}
    for s in scores:
        detail = s.get("detail") or {}
        for err in detail.get("phoneme_errors") or []:
            exp = str(err.get("expected") or "")
            heard = str(err.get("heard") or "")
            kind = str(err.get("type") or "sub")
            if EN_PHONES:
                if kind == "ins":
                    if heard not in EN_PHONES:
                        continue
                elif exp not in EN_PHONES:
                    continue
            tally[(exp, heard, kind)] = tally.get((exp, heard, kind), 0) + 1
    rows = [{"expected": k[0], "heard": k[1], "type": k[2], "count": v}
            for k, v in sorted(tally.items(), key=lambda kv: -kv[1])][:limit]
    return rows


def worst_segments(scores: List[dict], limit: int = 15, threshold: float = 95.0) -> List[dict]:
    """最差的一批句子（按历史最高分排，最高分低说明一直没读好）。"""
    agg: Dict[tuple, Dict[str, Any]] = {}
    for s in scores:
        key = (s["project_id"], int(s["seg_idx"]))
        e = agg.setdefault(key, {"project_id": key[0], "seg_idx": key[1], "best": 0.0, "tries": 0,
                                 "last": 0.0, "last_at": ""})
        score = float(s["score"] or 0)
        e["best"] = max(e["best"], score)
        e["tries"] += 1
        if (s.get("created_at") or "") >= (e["last_at"] or ""):
            e["last_at"] = s.get("created_at") or ""
            e["last"] = score
    rows = [e for e in agg.values() if e["best"] < threshold]
    rows.sort(key=lambda e: (e["best"], -e["tries"]))
    rows = rows[:limit]

    seg_cache: Dict[str, Dict[int, dict]] = {}
    title_cache: Dict[str, str] = {}
    for e in rows:
        pid = e["project_id"]
        if pid not in seg_cache:
            seg_cache[pid] = {int(s["idx"]): s for s in db.list_segments(pid)}
            title_cache[pid] = (db.get_project(pid) or {}).get("title") or pid
        seg = seg_cache[pid].get(e["seg_idx"]) or {}
        e["text"] = seg.get("text") or ""
        e["project_title"] = title_cache[pid]
        e["start"] = float(seg.get("start") or 0)
    return rows


def by_project(scores: List[dict]) -> List[dict]:
    agg: Dict[str, Dict[str, Any]] = {}
    for s in scores:
        e = agg.setdefault(s["project_id"], {"project_id": s["project_id"], "attempts": 0,
                                             "sum": 0.0, "best": 0.0, "seconds": 0.0})
        score = float(s["score"] or 0)
        e["attempts"] += 1
        e["sum"] += score
        e["best"] = max(e["best"], score)
        e["seconds"] += float((s.get("detail") or {}).get("duration") or 0)
    out = []
    for e in agg.values():
        project = db.get_project(e["project_id"]) or {}
        out.append({
            "project_id": e["project_id"],
            "title": project.get("title") or e["project_id"],
            "attempts": e["attempts"],
            "avg_score": round(e["sum"] / e["attempts"], 1) if e["attempts"] else 0.0,
            "best": round(e["best"], 1),
            "minutes": round(e["seconds"] / 60, 1),
        })
    out.sort(key=lambda x: -x["attempts"])
    return out


def mode_split(scores: List[dict]) -> List[dict]:
    agg: Dict[str, int] = {}
    for s in scores:
        agg[s.get("mode") or "repeat"] = agg.get(s.get("mode") or "repeat", 0) + 1
    names = {"repeat": "逐句跟读", "shadow": "影子跟读", "word": "单词", "phoneme": "音素练习"}
    return [{"mode": k, "label": names.get(k, k), "count": v}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


# --------------------------------------------------------------------------
def word_report(days: int = 120, drills: Optional[List[dict]] = None) -> Dict[str, Any]:
    """单词训练的统计：练了多少、哪个对立最分不开、队列压力。

    单词不绑项目，所以这一段永远是全局的 —— 即使调用方按项目过滤了句子。
    """
    if drills is None:
        drills = db.list_pair_drills(days=days, limit=50000)
    stats = db.pair_drill_stats()

    by_kind: Dict[str, Dict[str, int]] = {}
    for d in drills:
        k = d.get("kind") or "say"
        e = by_kind.setdefault(k, {"n": 0, "ok": 0})
        e["n"] += 1
        e["ok"] += 1 if d.get("correct") else 0
    kinds = [{"kind": k, "label": "听音辨词" if k == "listen" else "跟读对比",
              "count": v["n"], "ok_rate": round(100.0 * v["ok"] / v["n"], 1) if v["n"] else 0.0}
             for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]["n"])]

    hardest: List[dict] = []
    for pair_id, per_kind in stats.items():
        pair = MP.find(pair_id) or {}
        total = sum(int(v.get("n") or 0) for v in per_kind.values())
        if not total:
            continue
        ok = sum(int(v.get("ok") or 0) for v in per_kind.values())
        avgs = [float(v.get("avg_score") or 0) for v in per_kind.values() if v.get("n")]
        hardest.append({
            "pair_id": pair_id,
            "a": pair.get("a") or "", "b": pair.get("b") or "",
            "pa": pair.get("pa") or "", "pb": pair.get("pb") or "",
            "group": pair.get("group") or "",
            "attempts": total, "ok": ok,
            "ok_rate": round(100.0 * ok / total, 1),
            "avg_score": round(sum(avgs) / len(avgs), 1) if avgs else 0.0,
        })
    hardest.sort(key=lambda r: (r["ok_rate"], -r["attempts"]))
    return {
        "attempts": len(drills),
        "kinds": kinds,
        "hardest": hardest[:12],
        "pairs_practiced": len(hardest),
        "pairs_total": len(MP.PAIRS),
        "queue": srs.word_counts(),
    }


# --------------------------------------------------------------------------
def report(pid: Optional[str] = None, days: int = 120) -> Dict[str, Any]:
    """统计面板要的全部数据，一次给完（本地 SQLite，量很小，不用分页）。"""
    scores = _all_scores(pid)
    practice = db.list_practice(pid=pid, limit=50000)
    drills = db.list_pair_drills(days=days, limit=50000)
    return {
        "overview": overview(scores, practice, pid, drills),
        "daily": daily(scores, practice, days=days, drills=drills),
        "distribution": distribution(scores),
        "weak_words": weak_words(scores),
        "weak_phonemes": weak_phonemes(scores),
        "worst_segments": worst_segments(scores),
        "by_project": by_project(scores),
        "modes": mode_split(scores),
        "words": word_report(days=days, drills=drills),
        "review": {
            "counts": srs.counts(pid),
            "forecast": srs.forecast(pid, days=14),
        },
    }
