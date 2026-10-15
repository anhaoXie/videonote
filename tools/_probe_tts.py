# -*- coding: utf-8 -*-
"""排查：怎么合成才能让 ship / sheep、full / fool 真正听得出区别。

试三种方式：拼写合成、慢速拼写、直接给 IPA（espeak 的 [[...]] 音素输入）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.core import phonemes as P     # noqa: E402
from videonote.core import tts                # noqa: E402


def resample(x, sr_in, sr_out=16000):
    if sr_in == sr_out or x is None:
        return x
    idx = np.linspace(0, len(x) - 1, int(len(x) * sr_out / sr_in))
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def probe(label, text, wpm=150):
    wav = tts.synth(text, wpm=wpm)
    if wav is None:
        print(f"   {label:34} 合成失败")
        return
    dur = len(wav) / tts._sample_rate()
    rms = float(np.sqrt(np.mean(wav ** 2)))
    w16 = resample(wav, tts._sample_rate())
    # 用「拼写对应的期望音素」评估（对 IPA 输入，期望就是原文里的音素）
    exp = P._clean_phones(text[2:-2]) if text.startswith("[[") else P.word_to_phones(text)
    res = P.assess_audio(w16, exp)
    heard = " ".join(res.get("heard") or []) if res.get("ok") else f"x({res.get('reason')})"
    print(f"   {label:34} {dur:.2f}s RMS={rms:.3f}  期望[{' '.join(exp)}]  模型听到[{heard}]"
          f"  准确率 {res.get('accuracy')}")


print("=== A. 拼写合成 ===")
for w in ("ship", "sheep", "full", "fool", "fit", "feet"):
    probe(w, w)

print("\n=== B. 慢速拼写（wpm=110）===")
for w in ("ship", "sheep", "full", "fool"):
    probe(w + " (110)", w, wpm=110)

print("\n=== C. 直接给 IPA（[[...]] 音素输入）===")
pairs = [("ship", "[[ʃɪp]]"), ("sheep", "[[ʃiːp]]"),
         ("full", "[[fʊl]]"), ("fool", "[[fuːl]]"),
         ("think", "[[θɪŋk]]"), ("sink", "[[sɪŋk]]")]
for label, ipa in pairs:
    probe(label, ipa)

print("\n=== D. IPA 输入 + 慢速 ===")
for label, ipa in [("ship", "[[ʃɪp]]"), ("sheep", "[[ʃiːp]]")]:
    probe(label + " (110)", ipa, wpm=110)
