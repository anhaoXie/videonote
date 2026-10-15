# -*- coding: utf-8 -*-
"""音素层自检：G2P（espeak-ng / CMU 词典）-> 模型 -> CTC 强制对齐 -> GOP 判定。

不需要服务，直接调内部模块：
    .venv\\Scripts\\python.exe tools\\test_phonemes.py
用真实语音（项目里已有的英文音频）来验证，不用合成音。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                              # noqa: E402
from videonote.core import phonemes as P              # noqa: E402
from videonote.core.audio import read_wav_float32     # noqa: E402

FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def ascii_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if ord(c) < 128) / len(text)


VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v")


def find_english_project():
    """找一个以英文为主的、有音频产物的项目（优先真人录音=原始媒体是视频）。"""
    best = None
    for p in db.list_projects():
        segs = db.list_segments(p["id"])
        if not segs:
            continue
        ratio = ascii_ratio(" ".join(s.get("text") or "" for s in segs))
        art = db.get_artifact(p["id"], "audio")
        if not art or not art.get("path") or not Path(art["path"]).exists():
            continue
        if ratio < 0.7:
            continue
        real = (p.get("media_path") or "").lower().endswith(VIDEO_EXT)
        key = (0 if real else 1, -ratio)
        if best is None or key < best[0]:
            best = (key, ratio, p, segs, Path(art["path"]))
    return best[1:] if best else None


def main() -> int:
    print("=== 1. 环境 ===")
    ok, why = P.deps_ok()
    check("依赖齐全", ok, why)
    if not ok:
        return 1
    print(f"   模型目录: {P.model_dir()}")
    print(f"   词表就绪: {P.tokenizer_ready()}   权重就绪: {P.model_ready()}")
    avail, reason = P.available()
    check("音素评测可用（含权重）", avail, reason)

    print("\n=== 2. 英文音标（G2P）===")
    cases = {
        "think": ["θ", "ɪ", "ŋ", "k"],
        "very": ["v", "i"],
        "the": ["ð", "ə"],
        "right": ["ɹ", "aɪ", "t"],
        "cat": ["k", "æ", "t"],
        "going": ["ɡ", "oʊ", "ɪ", "ŋ"],
        "pronunciation": ["p", "ɹ", "ə"],
    }
    for word, expect in cases.items():
        got = P.word_to_phones(word)
        allin = all(ph in got for ph in expect)
        check(f"{word:14} -> {' '.join(got)}", bool(got) and allin,
              "" if allin else f"期望包含 {' '.join(expect)}")
    src = P._state.get("g2p_sources", {})
    print(f"   音标来源: {src}")
    check("用的是 espeak（和模型训练标签同一套）", src.get("think") == "espeak",
          f"think 来源={src.get('think')}")

    print("\n=== 3. 词表映射 ===")
    p2i, i2p = P.phone_id_maps()
    check("音素->id 映射非空", len(p2i) > 30, f"{len(p2i)} 个英文音素可映射")
    check("blank id 读到了", isinstance(P._state.get("blank"), int), f"blank={P._state.get('blank')}")
    missing = [c for c in ("θ", "ð", "ɹ", "ŋ", "æ", "iː", "uː", "tʃ", "aɪ", "oʊ") if c not in p2i]
    check("关键音素都在词表里", not missing, f"缺: {missing}" if missing else "")

    print("\n=== 4. 编辑距离 ===")
    ops = P.nw_align(["θ", "ɪ", "ŋ", "k"], ["s", "ɪ", "ŋ", "k"])
    subs = [o for o in ops if o["type"] == "sub"]
    check("θ->s 判为替换而非删+插", len(subs) == 1 and subs[0]["expected"] == "θ", str(subs))
    ins = P.nw_align(["k", "æ", "t"], ["k", "æ", "t", "ə"])
    check("多读的音被判为 ins", any(o["type"] == "ins" for o in ins))
    tips = P.tips_for({"phones": [{"p": "θ", "status": "sub", "heard": "s"}], "insertions": []})
    check("能给出「θ 读成 s」的发音要点", bool(tips) and "咬舌" in tips[0], tips[0] if tips else "")

    if not avail:
        print("\n⏳ 模型不完整，跳过后面的实测。")
        return 1 if FAIL else 0

    print("\n=== 5. 真实语音：整句音素对齐 + GOP ===")
    found = find_english_project()
    if not found:
        print("   ⚠ 没有找到英文项目的音频产物，跳过")
    else:
        ratio, project, segs, wav_path = found
        print(f"   用项目「{project['title']}」（英文占比 {ratio:.0%}）")
        wav = read_wav_float32(wav_path)
        print(f"   音频 {wav_path.name}，{len(wav) / 16000:.1f} 秒")

        # 整句评估：这才是生产路径（用户录一整句 -> 对着整句音素对齐）
        # 这里用参考音频当「用户完美跟读」的录音
        seg = max(segs, key=lambda s: float(s["end"]) - float(s["start"]))
        text = seg.get("text") or ""
        expected = P.text_to_phones(text)
        a = max(0.0, float(seg["start"]) - 0.1)
        b = float(seg["end"]) + 0.1
        clip = wav[int(a * 16000):int(b * 16000)]
        print(f"   句子（{b - a:.1f} 秒）: {text[:80]}")
        print(f"   期望音素 {len(expected)} 个: {' '.join(expected[:24])}{' …' if len(expected) > 24 else ''}")
        t0 = time.time()
        res = P.assess_audio(clip, expected)
        elapsed = time.time() - t0
        check("整句评估返回 ok", bool(res.get("ok")), str(res.get("reason", "")))
        if res.get("ok"):
            dur = len(clip) / 16000
            print(f"   识别音素: {' '.join(res['heard'][:24])}{' …' if len(res['heard']) > 24 else ''}")
            bad = [p for p in res["phones"] if p["status"] != "ok"]
            print(f"   音素准确率 {res['accuracy']}%（{len(res['phones']) - len(bad)}/{len(res['phones'])} 正确）")
            print(f"   ⏱ {dur:.1f}s 音频 / {elapsed:.2f}s 推理 (RTF {elapsed / max(0.01, dur):.2f})")
            if bad:
                print("   判为有问题的音素（前 10 个）:")
                for p in bad[:10]:
                    print(f"     /{p['p']}/  {p['status']:5} 听过={p['heard'] or '-':5} GOP={p['gop']:6.2f}")
            check("清晰的连续语音能对上大部分音素（≥50%）", res["accuracy"] >= 50,
                  f"实得 {res['accuracy']}%")
            check("每个音素都有帧区间", all(p["t"][1] > p["t"][0] for p in res["phones"]))
            check("CPU 上的推理速度可接受（RTF < 1.5）", elapsed / max(0.01, dur) < 1.5,
                  f"RTF {elapsed / max(0.01, dur):.2f}")

    print("\n=== 6. 两级流水线入口 assess_words（对可疑词逐词推理）===")
    if found:
        segs = found[2]
        seg = max(segs, key=lambda s: float(s["end"]) - float(s["start"]))
        items = []
        for w in (seg.get("words") or []):
            tok = "".join(c for c in str(w.get("w") or "") if c.isalpha())
            dur = float(w.get("end") or 0) - float(w.get("start") or 0)
            if len(tok) >= 3 and dur >= 0.2 and P.word_to_phones(tok):
                items.append({"word": tok, "start": w.get("start"), "end": w.get("end"),
                              "problem": "wrong"})
            if len(items) >= 2:
                break
        if items:
            t0 = time.time()
            out = P.assess_words(wav, 16000, items)
            el = time.time() - t0
            check("assess_words 每个词都返回结果", len(out) == len(items))
            for r in out:
                if not r.get("ok"):
                    print(f"   {str(r.get('word')):14} 未判定: {r.get('reason')}")
                    continue
                print(f"   {r.get('word'):14} 准确率={r.get('accuracy')}%  "
                      f"期望[{' '.join(r.get('expected') or [])}] 识别[{' '.join(r.get('heard') or [])}]")
                for p in (r.get("phones") or [])[:8]:
                    print(f"        /{p['p']}/ {p['status']:5} 听过={p['heard'] or '-'} GOP={p['gop']}")
                tips = P.tips_for(r)
                if tips:
                    print(f"        💡 {tips[0]}")
            check("结果的字段够前端渲染",
                  all("phones" in r and "accuracy" in r for r in out))
            print(f"   ⏱ {len(items)} 个词 / {el:.2f}s")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ 音素评测层全部通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
