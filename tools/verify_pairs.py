# -*- coding: utf-8 -*-
"""验证最小对立对的合成音「真的听得出区别」。

思路：espeak 合成 a、b 两个词 -> 音素模型各自解码 -> 判断模型能不能听出差异。
能听出（两个词的解码不同，且各自的区分音素被听到）才收录进训练库，
否则用户跟着练就是跟着错的示范练。

结果写到 data/minimal_pairs_verified.json，前端/接口只发验证过的词对。

用法：
    .venv\\Scripts\\python.exe tools\\verify_pairs.py            # 全部重验
    .venv\\Scripts\\python.exe tools\\verify_pairs.py --id v-w   # 只验一对
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.core import minimal_pairs as MP     # noqa: E402
from videonote.core import phonemes as P           # noqa: E402
from videonote.core import tts                     # noqa: E402


def resample(x: np.ndarray, sr_in: int, sr_out: int = 16000) -> np.ndarray:
    """用 tts 里那套带限重采样（线性插值会混叠，音素模型会听不懂）。"""
    return tts.resample(x, sr_in, sr_out)


def hear(word: str) -> dict:
    """合成一个词，让音素模型听回来。"""
    wav = tts.synth(word)
    if wav is None:
        return {"ok": False, "reason": "合成失败", "heard": []}
    w16 = resample(wav, tts._sample_rate())
    exp = P.word_to_phones(word)
    res = P.assess_audio(w16, exp)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason", ""), "heard": [], "expected": exp}
    heard = res.get("heard") or []
    # 模型听到的「主要音素」= 期望音素里被判为 ok/weak 的那些，加上自由解码
    ok_phones = [p["p"] for p in res["phones"] if p["status"] in ("ok", "weak")]
    return {"ok": True, "expected": exp, "heard": heard, "ok_phones": ok_phones,
            "accuracy": res["accuracy"], "problems": res.get("problems", 0)}


def judge(pair: dict) -> dict:
    """一对词是否合格：两个词的听感必须不同，且各自的区分音素都被听到。"""
    a, b = pair["a"], pair["b"]
    ha, hb = hear(a), hear(b)
    if not (ha.get("ok") and hb.get("ok")):
        return {"ok": False, "why": "合成/识别失败", "a": ha, "b": hb}
    ka, kb = ha["ok_phones"], hb["ok_phones"]
    # 1) 区分音素要被听到（a 里听到 pa，b 里听到 pb）
    has_pa = pair["pa"] in ka or pair["pa"] in ha["heard"]
    has_pb = pair["pb"] in kb or pair["pb"] in hb["heard"]
    # 2) 两个词不能听成一模一样
    distinct = (ha["heard"] != hb["heard"]) or (ka != kb)
    # 3) 不能互相串（a 不该听到 b 的区分音素）
    crossed = (pair["pb"] in ha["heard"]) or (pair["pa"] in hb["heard"])
    ok = has_pa and has_pb and distinct and not crossed
    why = ""
    if not ok:
        reasons = []
        if not has_pa:
            reasons.append(f"{a} 没听到 /{pair['pa']}/")
        if not has_pb:
            reasons.append(f"{b} 没听到 /{pair['pb']}/")
        if not distinct:
            reasons.append("两个词听感相同")
        if crossed:
            reasons.append("两个词互相串音")
        why = "；".join(reasons)
    return {"ok": ok, "why": why, "a": ha, "b": hb,
            "distinct": distinct, "has_pa": has_pa, "has_pb": has_pb, "crossed": crossed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="")
    args = ap.parse_args()

    db_ok = P.model_ready()
    print(f"音素模型就绪: {db_ok}    TTS 可用: {tts.available()}")
    if not db_ok or not tts.available():
        print("❌ 需要先装好音素模型（tools/get_phoneme_model.py）和 espeak")
        return 1

    targets = [p for p in MP.PAIRS if not args.id or p["id"] == args.id]
    results = {}
    t0 = time.time()
    good = 0
    for i, pair in enumerate(targets, 1):
        r = judge(pair)
        results[pair["id"]] = {
            "ok": r["ok"], "why": r["why"], "group": pair["group"],
            "a": pair["a"], "b": pair["b"], "pa": pair["pa"], "pb": pair["pb"],
            "heard_a": r["a"].get("heard"), "heard_b": r["b"].get("heard"),
            "ok_phones_a": r["a"].get("ok_phones"), "ok_phones_b": r["b"].get("ok_phones"),
        }
        if r["ok"]:
            good += 1
        mark = "✅" if r["ok"] else "❌"
        print(f"{mark} [{i:2}/{len(targets)}] {pair['a']:10} / {pair['b']:10} "
              f"({pair['pa']} / {pair['pb']})  听到 "
              f"[{' '.join(r['a'].get('heard') or [])}] / [{' '.join(r['b'].get('heard') or [])}]"
              + (f"   ✗ {r['why']}" if not r["ok"] else ""))

    out = MP.verified_path()
    existing = MP.load_verified()
    if args.id:
        existing.update(results)
        results = existing
    payload = {
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results), "passed": sum(1 for v in results.values() if v.get("ok")),
        "pairs": results,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n合格 {good}/{len(targets)} 对，用时 {time.time() - t0:.1f} 秒")
    print(f"结果已写入 {out}")
    print(f"库里可用的词对总数: {payload['passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
