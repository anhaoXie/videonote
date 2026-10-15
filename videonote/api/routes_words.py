# -*- coding: utf-8 -*-
"""单词级训练接口：最小对立对（ship/sheep、think/sink）。

两种模式：
  * 听音辨词（kind=listen）—— 放一个词，你选是 A 还是 B。练「耳朵分不分得开」。
    只出「合成音被音素模型确认过能分辨」的词对，否则就是让人瞎猜。
  * 跟读对比（kind=say）—— 你读一个词，音素模型告诉你那个关键音对不对。
    判分依据是 IPA 目标（不是合成音），所以 35 对全部可用。

音频由 espeak-ng 现场合成并缓存（离线、零成本）。机器音只作示意，
真正的判定永远发生在你自己的录音上。

每次练习除了写 pair_drills（历史流水），还会推进 SM-2 单词卡（word_reviews）——
复习页里「今天该练的词」就是从那张表来的，见 core/srs.py。
"""
from __future__ import annotations

import random
import time
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import settings
from ..core import analytics, minimal_pairs as MP, phonemes, srs, tts
from ..core.audio import decode_to_wav, read_wav_float32, wav_duration
from ..core.registry import register_router

router = APIRouter(tags=["words"])
register_router(router)


# --------------------------------------------------------------------------
# 词库
# --------------------------------------------------------------------------
@router.get("/words/pairs")
def list_pairs(group: str = "", verified_only: bool = False, limit: int = 200):
    """返回词对清单。verified_only=true 只给「音素模型确认能分辨」的（听音辨词用）。"""
    pairs = MP.all_pairs(only_verified=verified_only)
    if group:
        pairs = [p for p in pairs if p["group"] == group]
    stats = db.pair_drill_stats()
    for p in pairs:
        p["stats"] = stats.get(p["id"], {})
    heard = tts.available()
    return {
        "pairs": pairs[:limit],
        "total": len(pairs),
        "groups": MP.groups(),
        "tts_available": heard,
        "verified_total": len(MP.all_pairs(only_verified=True)),
        "note": "机器音由 espeak 合成，只作示意；判定用的是你自己的录音 + IPA 目标。",
    }


@router.get("/words/groups")
def list_groups():
    v = MP.load_verified()
    stats = db.pair_drill_stats()
    out = []
    for g in MP.groups():
        items = [p for p in MP.PAIRS if p["group"] == g["name"]]
        done = sum(1 for p in items if stats.get(p["id"]))
        out.append({**g, "practiced": done,
                    "verified": sum(1 for p in items if v.get(p["id"], {}).get("ok"))})
    return {"groups": out}


@router.get("/words/audio")
def word_audio(word: str = Query(..., min_length=1), wpm: int = 150, slow: bool = False):
    """合成（或读缓存）一个词的音频。"""
    word = word.strip()[:60]
    if not word:
        raise HTTPException(400, "word 不能为空")
    if not tts.available():
        raise HTTPException(503, "espeak 不可用，无法合成音频")
    path = tts.ensure_word(word, wpm=110 if slow else wpm)
    if not path or not path.exists():
        raise HTTPException(500, f"合成 {word!r} 失败")
    return FileResponse(str(path), media_type="audio/wav",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/words/plan")
def plan(limit: int = 12):
    """个性化计划：优先练你实际读错的音，再补上从没练过的。"""
    stats = db.pair_drill_stats()
    rep = analytics.report(days=90)
    weak = [x for x in (rep.get("weak_phonemes") or [])]
    weak_order: List[str] = []
    for w in weak:
        for pid in MP.matching_contrast(w.get("expected") or ""):
            if pid not in weak_order:
                weak_order.append(pid)

    verified = {p["id"]: p for p in MP.all_pairs(only_verified=True)}
    allp = MP.all_pairs(only_verified=False)
    picked: List[dict] = []

    def push(p: dict, why: str, **extra):
        if any(x["id"] == p["id"] for x in picked):
            return
        item = dict(p)
        item["why"] = why
        item["has_audio"] = p["id"] in verified
        item.update(extra)
        picked.append(item)

    # 0) 复习队列里已经到期的词卡 —— 最该先做，SM-2 已经算过它什么时候该来
    due_cards = srs.word_due_queue(limit=max(1, limit))
    for c in due_cards:
        p = MP.find(c["pair_id"])
        if p:
            push({**p, "verified": p["id"] in verified},
                 f"复习队列到期：{c['word']}", card_id=c["id"], card_word=c["word"])

    for pid in weak_order:                       # 1) 你读错过的音
        p = MP.find(pid)
        if p:
            push({**p, "verified": pid in verified}, "你之前读错过这个音")
    for p in allp:                               # 2) 练过但不熟的
        if len(picked) >= limit:
            break
        s = stats.get(p["id"], {}).get("say") or {}
        if s and s.get("n", 0) and (s.get("avg_score") or 0) < 80:
            push({**p, "verified": p["id"] in verified}, "上次读得还不够好")
    for p in allp:                               # 3) 从没练过的
        if len(picked) >= limit:
            break
        if not stats.get(p["id"]):
            push({**p, "verified": p["id"] in verified}, "还没练过")
    return {"plan": picked[:limit], "weak_phonemes": weak[:8], "stats": stats,
            "due_words": len(due_cards)}


# --------------------------------------------------------------------------
# 练习记录
# --------------------------------------------------------------------------
@router.post("/words/listen")
def record_listen(pair_id: str = Form(...), chosen: str = Form(...),
                  answer: str = Form(...), seconds: float = Form(0)):
    """听音辨词的作答。correct 由前端比对，这里只记账 + 推进复习卡。"""
    if not MP.find(pair_id):
        raise HTTPException(404, f"没有这个词对：{pair_id}")
    correct = str(chosen).strip().lower() == str(answer).strip().lower()
    db.add_pair_drill(pair_id, word=answer, kind="listen", correct=correct, score=100.0 if correct else 0.0,
                      expected=answer, heard=chosen,
                      detail={"chosen": chosen, "answer": answer, "seconds": seconds})
    # 听音辨词也是对「这个词」的证据，一起推进间隔重复
    review = srs.word_note(pair_id, answer, srs.score_listen_drill(correct),
                           key_hit=correct, mode="listen", seconds=float(seconds or 0))
    return {"ok": True, "correct": correct, "review": review}


@router.post("/words/say")
async def score_word(pair_id: str = Form(...), word: str = Form(...),
                     file: UploadFile = File(...)):
    """跟读一个词 -> 音素级判定（哪个音发错了）。"""
    pair = MP.find(pair_id)
    if not pair:
        raise HTTPException(404, f"没有这个词对：{pair_id}")
    if not phonemes.enabled():
        raise HTTPException(400, "音素评测未启用")
    ok, why = phonemes.available()
    if not ok:
        raise HTTPException(400, why)

    t0 = time.time()
    data = await file.read()
    if len(data) < 800:
        raise HTTPException(400, "录音太短，按住说一下这个词")
    tmp_dir = settings.data_dir / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw = tmp_dir / f"word_{pair_id}_{stamp}.upload"
    wav = tmp_dir / f"word_{pair_id}_{stamp}.wav"
    raw.write_bytes(data)
    try:
        decode_to_wav(raw, wav, 16000)
    except Exception as exc:  # noqa: BLE001
        raw.unlink(missing_ok=True)
        raise HTTPException(400, f"音频解码失败：{exc}") from exc
    finally:
        if raw.resolve() != wav.resolve():
            raw.unlink(missing_ok=True)

    samples = read_wav_float32(wav)
    expected = phonemes.word_to_phones(word)
    if not expected:
        wav.unlink(missing_ok=True)
        raise HTTPException(400, f"取不到 {word!r} 的音标")
    res = phonemes.assess_audio(samples, expected)
    if not res.get("ok"):
        raise HTTPException(400, res.get("reason") or "无法评估这段录音")

    # 这个词里「关键音」有没有读对，才是这次练习的重点
    key_phone = pair["pa"] if word.strip().lower() == pair["a"] else pair["pb"]
    key_hit = False
    for p in res.get("phones") or []:
        if p["p"] == key_phone and p["status"] in ("ok", "weak"):
            key_hit = True
    problems = [p for p in res.get("phones") or [] if p["status"] in ("sub", "del")]
    score = float(res.get("accuracy") or 0)
    tips = phonemes.tips_for(res)
    elapsed = round(time.time() - t0, 2)
    db.add_pair_drill(pair_id, word=word, kind="say", correct=key_hit, score=score,
                      expected=" ".join(expected), heard=" ".join(res.get("heard") or []),
                      detail={"phones": res.get("phones"), "accuracy": res.get("accuracy"),
                              "key_phone": key_phone, "key_hit": key_hit,
                              "problems": len(problems), "tips": tips,
                              "seconds": elapsed})
    # 关键音读错就把复习分压到及格线下：整词 accuracy 会掩盖它（think→sink 也有约 50%）
    word_score = srs.score_word_drill(score, key_hit)
    review = srs.word_note(pair_id, word, word_score, key_hit=key_hit, mode="say", seconds=elapsed)
    wav.unlink(missing_ok=True)
    return {
        "ok": True, "word": word, "pair_id": pair_id, "key_phone": key_phone,
        "key_hit": key_hit, "score": score, "accuracy": res.get("accuracy"),
        "word_score": word_score, "review": review,
        "expected": expected, "heard": res.get("heard"), "phones": res.get("phones"),
        "problems": len(problems), "tips": tips, "seconds": elapsed,
    }


@router.delete("/words/history")
def clear_history():
    db.execute("DELETE FROM pair_drills")
    return {"ok": True}
