# -*- coding: utf-8 -*-
"""发音评分 + 影子跟读分析接口。

前端把 MediaRecorder 录到的音频 POST 上来（webm/opus 或 mp4/aac），
这里解码 -> 本地 Whisper 回环识别 -> 和原句逐词对齐 -> 返回可解释的分数。
全过程不需要任何 API key。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..config import settings
from ..core import phonemes, srs
from ..core.audio import decode_to_wav, read_wav_float32, wav_duration
from ..core.registry import register_router
from ..core.scoring import align_words, phoneme_suspects, score_pronunciation, tokenize
from ..providers.base import get_asr_provider

router = APIRouter(tags=["practice"])

MAX_SHADOW_SECONDS = 600          # 影子跟读一次最多分析 10 分钟，防止 CPU 卡死


# --------------------------------------------------------------------------
def _recordings_dir(pid: str) -> Path:
    d = settings.project_dir(pid) / "recordings"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _save_and_decode(pid: str, upload: UploadFile, tag: str) -> Path:
    """把上传的录音存下来并解码成 16k wav。"""
    d = _recordings_dir(pid)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = Path(upload.filename or "rec.webm").suffix or ".webm"
    # 注意：上传的原始文件和解码产物必须不同名。之前 wav 上传时两者同名，
    # 解完码 raw.unlink() 会把结果删掉，导致后面读不到文件。
    raw = d / f"{tag}_{stamp}.upload{suffix}"
    wav = d / f"{tag}_{stamp}.wav"
    size = 0
    with raw.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 512)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
    if size < 800:
        raw.unlink(missing_ok=True)
        raise HTTPException(400, "录音太短或为空，再录一次试试")
    try:
        decode_to_wav(raw, wav, 16000)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"音频解码失败（浏览器录音格式可能不被支持）：{exc}") from exc
    if raw.resolve() != wav.resolve():
        raw.unlink(missing_ok=True)      # 只留 wav，省空间
    if not wav.exists():
        raise HTTPException(500, "音频解码后文件丢失了，请重试")
    return wav


def _transcribe(wav: Path):
    provider = get_asr_provider(None)
    tr = provider.transcribe(wav, language=None, progress=None)
    return tr


def _segment(pid: str, idx: int) -> dict:
    for s in db.list_segments(pid):
        if int(s["idx"]) == idx:
            return s
    raise HTTPException(404, f"找不到第 {idx} 句")


def analyze_phonemes(wav: Path, detail: dict, target_text: str) -> dict:
    """音素级精评：只对「读错的词」和「读得含糊的词」切片推理。

    返回 {"ok": bool, "words": [...], "errors": [...], "tips": [...], "reason": str}
    errors 是扁平化的音素错误列表，统计页直接用它做「最常读错的音」。
    """
    ok, why = phonemes.available()
    if not ok:
        return {"ok": False, "reason": why, "words": [], "errors": [], "tips": []}
    # 只对英文做音素评测：中文/日文用英文音标体系出来的是噪声
    letters = sum(1 for c in (target_text or "") if c.isascii() and c.isalpha())
    chars = sum(1 for c in (target_text or "") if not c.isspace())
    if chars == 0 or letters / chars < 0.5:
        return {"ok": False, "reason": "这句话不是英文，跳过音素分析（词级评分照常）",
                "words": [], "errors": [], "tips": []}
    try:
        samples = read_wav_float32(wav)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"读不到录音：{exc}", "words": [], "errors": [], "tips": []}

    suspects = phoneme_suspects(detail.get("words") or [],
                               max_words=settings.phoneme_max_words)
    try:
        if suspects:
            results = phonemes.assess_words(samples, 16000, suspects)
        else:
            # 没有可疑词：整段对着整句音素做一次对齐（也能发现「整体含混」）
            expected = phonemes.text_to_phones(target_text)
            if not expected:
                return {"ok": False, "reason": "这句话取不到音标", "words": [], "errors": [], "tips": []}
            res = phonemes.assess_audio(samples, expected)
            results = [{"word": target_text.strip(), **res}] if res.get("ok") else []
            for r in results:
                r["problem"] = "sentence"
    except Exception as exc:  # noqa: BLE001 - 音素失败不影响词级评分
        return {"ok": False, "reason": f"音素推理失败：{exc}", "words": [], "errors": [], "tips": []}

    errors: List[dict] = []
    tips: List[str] = []
    words_out: List[dict] = []
    for r in results:
        if not r.get("ok"):
            words_out.append({"word": r.get("word"), "ok": False, "reason": r.get("reason")})
            continue
        if r.get("unclear"):
            # 模型在这段里没听到任何音素：多半是没录上，别判用户「漏读」
            words_out.append({"word": r.get("word"), "ok": False, "unclear": True,
                              "reason": "这段录音里几乎听不到语音，先确认麦克风录上了"})
            continue
        bad = [p for p in r.get("phones") or [] if p.get("status") in ("sub", "del", "weak")]
        for p in bad:
            errors.append({"expected": p.get("p"), "heard": p.get("heard"),
                           "type": "del" if p.get("status") == "del" else "sub",
                           "word": r.get("word"), "gop": p.get("gop"), "problem": r.get("problem")})
        for extra in r.get("insertions") or []:
            errors.append({"expected": "", "heard": extra, "type": "ins",
                           "word": r.get("word"), "problem": r.get("problem")})
        tips.extend(phonemes.tips_for(r))
        words_out.append({
            "word": r.get("word"), "ok": True, "problem": r.get("problem"),
            "accuracy": r.get("accuracy"), "expected": r.get("expected"), "heard": r.get("heard"),
            "phones": r.get("phones"), "insertions": r.get("insertions"),
        })
    # 去重 + 截断，别让建议刷屏
    seen = set()
    uniq_tips = []
    for t in tips:
        if t not in seen:
            seen.add(t)
            uniq_tips.append(t)
    return {"ok": True, "words": words_out, "errors": errors, "tips": uniq_tips[:5],
            "model": settings.phoneme_model, "checked": len([w for w in words_out if w.get("ok")])}


# --------------------------------------------------------------------------
# 逐句跟读评分
# --------------------------------------------------------------------------
@router.post("/projects/{pid}/score")
async def score_repeat(
    pid: str,
    file: UploadFile = File(...),
    seg_idx: int = Form(...),
    mode: str = Form("repeat"),
    asr_provider: str = Form(""),
):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    seg = _segment(pid, seg_idx)
    wav = await _save_and_decode(pid, file, f"seg{seg_idx}")

    provider = get_asr_provider(asr_provider or None)
    tr = provider.transcribe(wav, language=None, progress=None)
    user_words = []
    for s in tr.segments:
        user_words.extend(s.words or [])
    user_dur = tr.duration or wav_duration(wav)
    user_words = [{"w": str(w.get("w", "")), "start": float(w.get("start") or 0),
                   "end": float(w.get("end") or 0), "prob": float(w.get("prob") or 0)}
                  for w in user_words]

    result = score_pronunciation(
        target_text=seg.get("text") or "",
        target_words=seg.get("words") or [],
        target_duration=float(seg["end"]) - float(seg["start"]),
        user_text=tr.text,
        user_words=user_words,
        user_duration=user_dur,
        mode=mode,
    )
    result["engine"] = provider.describe()

    # 音素级精评（可选）：只对可疑词推理，CPU 上通常 <1 秒
    phon = {"ok": False, "reason": "未启用", "words": [], "errors": [], "tips": []}
    if phonemes.enabled():
        t_ph = time.time()
        phon = analyze_phonemes(wav, result["detail"], seg.get("text") or "")
        phon["seconds"] = round(time.time() - t_ph, 2)
    result["phonemes"] = phon
    if phon.get("ok"):
        result["advice"] = list(result["advice"]) + list(phon.get("tips") or [])

    row = db.add_score(
        pid, seg_idx=int(seg_idx),
        score=result["score"], accuracy=result["accuracy"], fluency=result["fluency"],
        completeness=result["completeness"], target=result["target"], transcript=result["transcript"],
        detail={**result["detail"], "advice": result["advice"], "engine": result["engine"],
                "phoneme_words": phon.get("words") or [],
                "phoneme_errors": phon.get("errors") or [],
                "phoneme_seconds": phon.get("seconds", 0)},
        recording=str(wav), mode=mode,
    )
    result["id"] = row["id"]
    result["recording_url"] = f"/api/scores/{row['id']}/recording"
    # 读得不好就自动进复习队列；读得好就推进下一次复习时间
    try:
        result["review"] = srs.note_score(pid, int(seg_idx), result["score"],
                                          seconds=float(user_dur or 0), mode=mode)
    except Exception as exc:  # noqa: BLE001 - 复习调度失败不该让评分失败
        result["review"] = {"error": str(exc)}
    return result


@router.get("/scores/{sid}/recording")
def score_recording(sid: int):
    row = db.query_one("SELECT recording FROM scores WHERE id=?", (sid,))
    if not row or not row.get("recording") or not Path(row["recording"]).exists():
        raise HTTPException(404, "录音文件不在了")
    return FileResponse(row["recording"], media_type="audio/wav")


@router.get("/projects/{pid}/scores")
def list_scores(pid: str, seg_idx: Optional[int] = None, limit: int = 100):
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return {"scores": db.list_scores(pid, seg_idx, limit), "best": db.best_scores(pid)}


# --------------------------------------------------------------------------
# 影子跟读：一段连续录音，算出「覆盖了多少句 / 平均滞后多少秒 / 每句表现」
# --------------------------------------------------------------------------
@router.post("/projects/{pid}/shadow/analyze")
async def analyze_shadow(
    pid: str,
    file: UploadFile = File(...),
    start_seg: int = Form(...),
    end_seg: int = Form(-1),
    video_start: float = Form(0.0),
    asr_provider: str = Form(""),
):
    """video_start = 开始录音时视频的播放位置（秒），用来把录音时间轴对齐到视频。"""
    if not db.get_project(pid):
        raise HTTPException(404, "项目不存在")
    segs = db.list_segments(pid)
    if not segs:
        raise HTTPException(400, "还没有转写结果")
    end_idx = end_seg if end_seg >= 0 else int(segs[-1]["idx"])
    picked = [s for s in segs if start_seg <= int(s["idx"]) <= end_idx]
    if not picked:
        raise HTTPException(400, "选中的句子范围是空的")

    wav = await _save_and_decode(pid, file, f"shadow{start_seg}")
    dur = wav_duration(wav)
    if dur > MAX_SHADOW_SECONDS:
        raise HTTPException(400, f"录音 {dur:.0f} 秒太长了，一次最多 {MAX_SHADOW_SECONDS} 秒")

    provider = get_asr_provider(asr_provider or None)
    t0 = time.time()
    tr = provider.transcribe(wav, language=None, progress=None)
    elapsed = time.time() - t0

    # 目标侧：句子的词（没有词级时间戳就按句长平均分配）
    target_tokens: List[str] = []
    target_meta: List[Dict[str, Any]] = []
    for s in picked:
        words = [w for w in (s.get("words") or []) if str(w.get("w", "")).strip()]
        if not words:
            toks = tokenize(s.get("text") or "")
            span = max(0.4, float(s["end"]) - float(s["start"]))
            step = span / max(1, len(toks))
            words = [{"w": t, "start": float(s["start"]) + i * step} for i, t in enumerate(toks)]
        for w in words:
            toks = tokenize(str(w.get("w", "")))
            for tk in toks:
                target_tokens.append(tk)
                target_meta.append({"seg": int(s["idx"]), "time": float(w.get("start") or 0)})

    # 用户侧
    user_words = []
    for s in tr.segments:
        for w in (s.words or []):
            # Segment.words 是 dict 列表（{w,start,end,prob}），不是对象
            for tk in tokenize(str(w.get("w", ""))):
                user_words.append({"w": tk, "start": float(w.get("start") or 0),
                                   "end": float(w.get("end") or 0)})
    if not user_words:
        toks = tokenize(tr.text or "")
        total = tr.duration or dur
        step = total / max(1, len(toks))
        user_words = [{"w": t, "start": i * step, "end": (i + 1) * step} for i, t in enumerate(toks)]

    user_tokens = [w["w"] for w in user_words]
    aligned = align_words(target_tokens, user_tokens)

    # 把对齐上的词对映射回句子，并计算滞后
    per_seg: Dict[int, Dict[str, Any]] = {}
    lags: List[float] = []
    for ti, ui in aligned["pairs"]:
        meta = target_meta[ti]
        seg_idx = meta["seg"]
        entry = per_seg.setdefault(seg_idx, {"matched": 0, "total": 0, "lags": []})
        lag = (user_words[ui]["start"] + video_start) - meta["time"]
        lags.append(lag)
        entry["lags"].append(lag)
        entry["matched"] += 1
    for tok_i, meta in enumerate(target_meta):
        entry = per_seg.setdefault(meta["seg"], {"matched": 0, "total": 0, "lags": []})
        entry["total"] += 1

    seg_rows = []
    for s in picked:
        e = per_seg.get(int(s["idx"]), {"matched": 0, "total": 0, "lags": []})
        total = max(1, e["total"])
        # 注意：matched 是在上面的循环里累加的，这里重新数一遍更直观
        matched = sum(1 for ti, _ in aligned["pairs"] if target_meta[ti]["seg"] == int(s["idx"]))
        e["matched"] = matched
        seg_lags = sorted(e["lags"])
        seg_lag = round(seg_lags[len(seg_lags) // 2], 2) if seg_lags else None
        seg_rows.append({
            "idx": int(s["idx"]),
            "text": s.get("text") or "",
            "start": float(s["start"]),
            "matched": matched,
            "total": total,
            "coverage": round(matched / total, 3),
            "lag": seg_lag,
        })

    matched_total = len(aligned["pairs"])
    coverage = matched_total / max(1, len(target_tokens))
    lags_sorted = sorted(lags)
    median_lag = round(lags_sorted[len(lags_sorted) // 2], 2) if lags_sorted else None
    weak = [r for r in seg_rows if r["coverage"] < 0.6]

    advice: List[str] = []
    if coverage >= 0.9:
        advice.append(f"整段覆盖 {coverage*100:.0f}%，跟得很稳")
    elif coverage >= 0.7:
        advice.append(f"覆盖 {coverage*100:.0f}%，漏了一些词，重点看下面标红的句子")
    else:
        advice.append(f"覆盖只有 {coverage*100:.0f}%，建议先降速到 0.75x 再跟")
    if median_lag is not None:
        if median_lag > 2.0:
            advice.append(f"平均滞后 {median_lag:.1f} 秒，偏多；影子跟读的目标是 1 秒以内")
        elif median_lag < -0.3:
            advice.append("你抢在音频前面了，稍微等一下再跟")
        else:
            advice.append(f"平均滞后 {median_lag:.1f} 秒，节奏不错")
    if weak:
        advice.append("跟丢最多的句子：" + "、".join(f"#{r['idx']+1}" for r in weak[:5]))

    detail = {
        "coverage": round(coverage, 4),
        "lag": median_lag,
        "wer": aligned["wer"],
        "segments": seg_rows,
        "advice": advice,
        "duration": round(dur, 2),
        "asr_seconds": round(elapsed, 1),
        "engine": provider.describe(),
        "missing": aligned["missing"][:40],
        "extra": aligned["extra"][:40],
    }
    row = db.add_score(
        pid, seg_idx=int(start_seg), score=round(coverage * 100, 1),
        accuracy=round((1 - aligned["wer"]) * 100, 1), fluency=round(max(0.0, 1 - abs(median_lag or 0) / 4) * 100, 1),
        completeness=round(coverage * 100, 1), target=f"句子 {start_seg}~{end_idx}",
        transcript=tr.text[:2000], detail=detail, recording=str(wav), mode="shadow",
    )
    detail["id"] = row["id"]
    detail["transcript"] = tr.text
    detail["recording_url"] = f"/api/scores/{row['id']}/recording"
    return detail


register_router(router)
