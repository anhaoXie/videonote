# -*- coding: utf-8 -*-
"""发音评分引擎（纯本地，零 API）。

思路：把用户的跟读录音再喂给 Whisper，得到「它听成了什么」，
再和原句做**逐词对齐**（difflib），算出三个维度：

    accuracy     念对了多少（多说/乱加词会扣分）
    completeness 原句有多少词被念出来（漏词扣分）
    fluency      语速与停顿是否自然（跟原句时长比 + 长停顿次数）

总分 = 0.6*accuracy + 0.25*fluency + 0.15*completeness

这不是音素级评测（那要 Azure/讯飞的付费接口，见 ROADMAP），
但它能准确回答「对方能不能听懂你」——对练口语来说这才是第一位的。
"""
from __future__ import annotations

import difflib
import re
import string
from typing import Any, Dict, List, Optional

_PUNCT = string.punctuation + "“”‘’—–…《》〈〉「」『』【】、。，！？；：（）·「」＂＇"
_TOKEN_RE = re.compile(r"[A-Za-z0-9']+|[\u4e00-\u9fa5]")
PAUSE_THRESHOLD = 1.1          # 超过这个秒数算一次「长停顿」
WEIGHTS = {"accuracy": 0.60, "fluency": 0.25, "completeness": 0.15}


def normalize_token(token: str) -> str:
    t = (token or "").strip().lower().strip(_PUNCT)
    t = t.replace("’", "'").replace("‘", "'")
    return t


def tokenize(text: str) -> List[str]:
    """英文按词、中文按字切；去掉标点和空白。"""
    return [t for t in (normalize_token(x) for x in _TOKEN_RE.findall(text or "")) if t]


def _norm_words(words: Optional[List[dict]]) -> List[dict]:
    out: List[dict] = []
    for w in words or []:
        tok = normalize_token(str(w.get("w", "")))
        if not tok:
            continue
        out.append({"w": tok, "start": float(w.get("start") or 0), "end": float(w.get("end") or 0),
                    "prob": float(w.get("prob") or 0)})
    return out


def align_words(target: List[str], user: List[str]) -> Dict[str, Any]:
    """逐词对齐，返回每个原句词的状态和统计。

    detail 里每个词都带 ti/ui（原句下标 / 用户录音里的下标，-1 表示没有对应）。
    音素级评测要用 ui 去用户录音里切那一段音频。
    """
    matcher = difflib.SequenceMatcher(None, target, user, autojunk=False)
    opcodes = matcher.get_opcodes()
    detail: List[Dict[str, Any]] = []
    matched = 0
    pairs: List[tuple] = []          # (target_idx, user_idx) 对齐上的词对

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            matched += i2 - i1
            for k in range(i2 - i1):
                detail.append({"w": target[i1 + k], "status": "ok", "heard": user[j1 + k],
                               "ti": i1 + k, "ui": j1 + k})
                pairs.append((i1 + k, j1 + k))
        elif tag == "replace":
            for k in range(i1, i2):
                ui = j1 + (k - i1)
                heard = user[ui] if ui < j2 else ""
                detail.append({"w": target[k], "status": "wrong", "heard": heard,
                               "ti": k, "ui": ui if ui < j2 else -1})
            for k in range(j1, j2):
                if k - j1 >= i2 - i1:
                    detail.append({"w": user[k], "status": "extra", "heard": user[k],
                                   "ti": -1, "ui": k})
        elif tag == "delete":
            for k in range(i1, i2):
                detail.append({"w": target[k], "status": "missing", "heard": "", "ti": k, "ui": -1})
        elif tag == "insert":
            for k in range(j1, j2):
                detail.append({"w": user[k], "status": "extra", "heard": user[k], "ti": -1, "ui": k})

    n_t = max(1, len(target))
    n_u = max(1, len(user))
    substitutions = sum(1 for d in detail if d["status"] == "wrong")
    deletions = sum(1 for d in detail if d["status"] == "missing")
    insertions = sum(1 for d in detail if d["status"] == "extra")
    wer = (substitutions + deletions + insertions) / n_t
    return {
        "detail": detail,
        "pairs": pairs,
        "matched": matched,
        "wer": round(wer, 4),
        "accuracy": round(matched / max(n_t, n_u), 4),
        "completeness": round(min(1.0, matched / n_t), 4),
        "missing": [d["w"] for d in detail if d["status"] == "missing"],
        "wrong": [{"target": d["w"], "heard": d["heard"]} for d in detail if d["status"] == "wrong"],
        "extra": [d["w"] for d in detail if d["status"] == "extra"],
    }


def _fluency(target_dur: float, user_dur: float, user_words: List[dict]) -> Dict[str, Any]:
    """语速 + 停顿。"""
    pauses = []
    for a, b in zip(user_words, user_words[1:]):
        gap = b["start"] - a["end"]
        if gap >= PAUSE_THRESHOLD:
            pauses.append(round(gap, 2))
    ratio = (user_dur / target_dur) if target_dur > 0.2 and user_dur > 0 else 1.0
    if ratio < 0.6:
        speed = 0.75                      # 太快，可能吞音
        note = "语速明显快于原句，注意别吞音"
    elif ratio <= 1.45:
        speed = 1.0
        note = "语速自然"
    elif ratio <= 2.0:
        speed = 0.7
        note = "比原句慢了不少，可以再顺一点"
    else:
        speed = 0.4
        note = "明显偏慢，多半是在逐词念而不是连读"
    pause_penalty = min(0.45, 0.15 * max(0, len(pauses) - 1))
    score = max(0.0, speed - pause_penalty)
    return {
        "score": round(score, 4),
        "duration_ratio": round(ratio, 3),
        "pauses": pauses,
        "note": note + (f"；句中有 {len(pauses)} 次长停顿" if pauses else ""),
    }


def _lag(target_words: List[dict], user_words: List[dict], pairs: List[tuple]) -> Optional[float]:
    """影子跟读：你的词比原句晚多少秒（正数=滞后）。"""
    if not pairs or not target_words or not user_words:
        return None
    t0 = target_words[0]["start"]
    offsets = []
    for ti, ui in pairs:
        if ti < len(target_words) and ui < len(user_words):
            offsets.append((user_words[ui]["start"] - 0.0) - (target_words[ti]["start"] - t0))
    if not offsets:
        return None
    offsets.sort()
    mid = len(offsets) // 2
    return round(offsets[mid] if len(offsets) % 2 else (offsets[mid - 1] + offsets[mid]) / 2, 3)


def score_pronunciation(
    *,
    target_text: str,
    target_words: Optional[List[dict]] = None,
    target_duration: float = 0.0,
    user_text: str = "",
    user_words: Optional[List[dict]] = None,
    user_duration: float = 0.0,
    mode: str = "repeat",
) -> Dict[str, Any]:
    """算出总分和可解释的逐词结果。"""
    t_tokens = tokenize(target_text)
    tw = _norm_words(target_words)
    uw = _norm_words(user_words)

    # 用户在录音里说的词（带时间戳）展开成 token 序列，并记住每个 token 属于哪个词；
    # 这样对齐出来的 ui 能反查回「这段话在录音的哪一秒」，音素评测就靠它切片。
    u_tokens: List[str] = []
    u_owner: List[int] = []
    for wi, w in enumerate(uw):
        toks = tokenize(w["w"]) or [w["w"]]
        for t in toks:
            u_tokens.append(t)
            u_owner.append(wi)
    if not u_tokens:
        u_tokens = tokenize(user_text)

    aligned = align_words(t_tokens, u_tokens)
    for d in aligned["detail"]:
        ui = int(d.get("ui", -1))
        if 0 <= ui < len(u_owner):
            w = uw[u_owner[ui]]
            d["start"] = w["start"]
            d["end"] = w["end"]
            d["prob"] = w["prob"]
    flu = _fluency(target_duration, user_duration, uw)

    score = (
        WEIGHTS["accuracy"] * aligned["accuracy"]
        + WEIGHTS["fluency"] * flu["score"]
        + WEIGHTS["completeness"] * aligned["completeness"]
    ) * 100.0

    advice: List[str] = []
    if aligned["missing"]:
        advice.append("漏词：" + "、".join(aligned["missing"][:6]))
    if aligned["wrong"]:
        advice.append("读错：" + "、".join(f"{w['target']}→(听成){w['heard']}" for w in aligned["wrong"][:5]))
    if aligned["extra"]:
        advice.append("多说的词：" + "、".join(aligned["extra"][:6]))
    if flu["note"] and flu["score"] < 0.95:
        advice.append(flu["note"])
    if not advice:
        advice.append("几乎与原句一致，很地道 👍")

    return {
        "score": round(score, 1),
        "accuracy": round(aligned["accuracy"] * 100, 1),
        "completeness": round(aligned["completeness"] * 100, 1),
        "fluency": round(flu["score"] * 100, 1),
        "wer": aligned["wer"],
        "target": target_text.strip(),
        "transcript": (user_text or "").strip(),
        "mode": mode,
        "advice": advice,
        "detail": {
            "words": aligned["detail"],
            "missing": aligned["missing"],
            "wrong": aligned["wrong"],
            "extra": aligned["extra"],
            "pauses": flu["pauses"],
            "duration": round(user_duration, 2),
            "target_duration": round(target_duration, 2),
            "duration_ratio": flu["duration_ratio"],
            "wpm": round((len(u_tokens) / user_duration) * 60, 1) if user_duration > 0.2 else 0,
            "lag": _lag(tw, uw, aligned["pairs"]),
        },
    }


def phoneme_suspects(detail_words: List[dict], max_words: int = 8,
                     low_prob: float = 0.6) -> List[dict]:
    """挑出「值得做音素级精评」的词。

    只挑两类：
      * 识别听成了别的词（status=wrong）——最可能是发音问题；
      * 虽然听对了但模型置信度很低（prob < low_prob）——多半读得含糊。
    时间区间（start/end）是 score_pronunciation 已经写在 detail 里的、
    **用户在录音里说这个词的时间**，音素模型只对这一小段推理。
    """
    out: List[dict] = []
    for d in detail_words or []:
        status = d.get("status")
        if d.get("start") is None or d.get("end") is None:
            continue
        if status == "wrong":
            problem = "wrong"
        elif status == "ok" and float(d.get("prob") or 0) < low_prob:
            problem = "low_prob"
        else:
            continue
        out.append({
            "word": d.get("w") or "",
            "start": float(d.get("start") or 0),
            "end": float(d.get("end") or 0),
            "prob": float(d.get("prob") or 0),
            "problem": problem,
            "ti": d.get("ti", -1),
            "ui": d.get("ui", -1),
        })
    return out[:max_words]


def render_word_html(detail: Dict[str, Any]) -> str:
    """把逐词结果渲染成带颜色的 HTML 片段（服务端就能生成，前端直接用）。"""
    colors = {"ok": "#22c55e", "wrong": "#f59e0b", "missing": "#ef4444", "extra": "#8b5cf6"}
    out = []
    for d in detail.get("words", []):
        c = colors.get(d["status"], "#e6e9ef")
        title = f' title="听到的是：{d.get("heard") or "(没听到)"}"' if d.get("heard") else ""
        out.append(f'<span style="color:{c}"{title}>{d["w"]}</span>')
    return " ".join(out)
