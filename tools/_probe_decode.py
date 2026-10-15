# -*- coding: utf-8 -*-
"""诊断：模型对真实音频片段的「自由解码」结果 vs 我的强制对齐。"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                                  # noqa: E402
from videonote.core import phonemes as P                  # noqa: E402
from videonote.core.audio import read_wav_float32         # noqa: E402


def decode_clip(wav, rate, a, b, tok):
    p2i, i2p = P.phone_id_maps()
    fe, model = P.load()
    torch = P._state["torch"]
    clip = wav[int(a * rate):int(b * rate)]
    with torch.inference_mode():
        inputs = fe(clip, sampling_rate=rate, return_tensors="pt")
        logits = model(inputs.input_values).logits[0].float().numpy()
    logp = P._log_softmax(logits)
    hyp = P.greedy_decode(logp, int(P._state["blank"]), i2p)
    exp = P.word_to_phones(tok)
    # 每帧 top1 的置信度
    top = np.exp(logp.max(-1))
    print(f"   切片 {a:.2f}-{b:.2f}s（{len(clip) / rate:.2f}s，{logits.shape[0]} 帧）")
    print(f"   期望音素 : {' '.join(exp)}")
    print(f"   自由解码 : {' '.join(hyp)}")
    print(f"   帧平均最高概率: {top.mean():.3f}   最低: {top.min():.3f}")
    # 强制对齐
    res = P.assess_audio(clip, exp, sr=rate)
    if res.get("ok"):
        got = " ".join(f"{p['p']}:{p['status']}({p['gop']:.1f})" for p in res["phones"])
        print(f"   强制对齐 : {got}")
    else:
        print(f"   强制对齐失败: {res.get('reason')}")
    return hyp


# 真实人声：JFK 演示项目；合成音：两说话人项目
targets = []
for p in db.list_projects():
    art = db.get_artifact(p["id"], "audio")
    if art and art.get("path") and Path(art["path"]).exists():
        targets.append((p, Path(art["path"])))

for p, path in targets:
    segs = db.list_segments(p["id"])
    print(f"\n=== {p['title']} ===")
    with wave.open(str(path), "rb") as f:
        rate = f.getframerate()
    wav = read_wav_float32(path)
    print(f"音频 {path.name}，{len(wav) / rate:.1f} 秒，采样率 {rate}")
    done = 0
    for s in segs:
        for w in (s.get("words") or []):
            tok = "".join(c for c in str(w.get("w") or "") if c.isalpha())
            dur = float(w.get("end") or 0) - float(w.get("start") or 0)
            if len(tok) >= 4 and dur >= 0.25 and P.word_to_phones(tok):
                a = max(0.0, float(w["start"]) - 0.25)
                b = float(w["end"]) + 0.25
                print(f"\n  [{s['idx']}] {tok!r} (原声 {dur:.2f}s, prob={float(w.get('prob') or 0):.2f})")
                decode_clip(wav, rate, a, b, tok)
                done += 1
                break
        if done >= 3:
            break
