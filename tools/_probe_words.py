# -*- coding: utf-8 -*-
"""诊断：词级时间戳和音频到底对不对得上。

对比两个项目里同一个词（fellow / country）的切片 RMS 和 Whisper 自由识别结果。
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                                  # noqa: E402
from videonote.core.audio import read_wav_float32         # noqa: E402

for p in db.list_projects():
    segs = db.list_segments(p["id"])
    art = db.get_artifact(p["id"], "audio")
    if not segs or not art or not art.get("path"):
        continue
    path = Path(art["path"])
    if not path.exists():
        continue
    with wave.open(str(path), "rb") as f:
        ch, rate, n = f.getnchannels(), f.getframerate(), f.getnframes()
    print(f"\n=== {p['title']}  ({p['id']}) ===")
    print(f"音频 {path.name}: {ch} 声道 / {rate} Hz / {n / rate:.1f} 秒")
    for s in segs[:3]:
        print(f"  句 {s['idx']}: {s['start']:.2f}-{s['end']:.2f}  「{s['text'][:50]}」")
        for w in (s.get("words") or [])[:8]:
            print(f"      {w.get('w'):16} {float(w.get('start') or 0):6.2f} - {float(w.get('end') or 0):6.2f}"
                  f"  prob={float(w.get('prob') or 0):.2f}")

    # 切片检查：第一个足够长的词
    wav = read_wav_float32(path)
    if len(wav) / rate < 1:
        continue
    for s in segs[:3]:
        for w in (s.get("words") or []):
            tok = "".join(c for c in str(w.get("w") or "") if c.isalpha())
            dur = float(w.get("end") or 0) - float(w.get("start") or 0)
            if len(tok) >= 4 and dur >= 0.25:
                a = max(0.0, float(w["start"]) - 0.25)
                b = float(w["end"]) + 0.25
                clip = wav[int(a * rate):int(b * rate)]
                rms = float(np.sqrt(np.mean(clip ** 2))) if len(clip) else 0.0
                print(f"\n  → 用 {tok!r} 切片 {a:.2f}-{b:.2f}s，长 {len(clip) / rate:.2f}s，RMS={rms:.4f}")
                try:
                    from videonote.providers.asr_local import FasterWhisperASR
                    tr = FasterWhisperASR().transcribe_wav_array(clip, rate) \
                        if hasattr(FasterWhisperASR, "transcribe_wav_array") else None
                except Exception as exc:  # noqa: BLE001
                    print("     (whisper 复检跳过:", exc, ")")
                    tr = None
                if tr is not None:
                    print("     Whisper 听到:", tr)
                break
        else:
            continue
        break
    break
