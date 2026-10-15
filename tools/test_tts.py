# -*- coding: utf-8 -*-
"""自检：espeak 合成 + 用音素模型反过来验证合成对不对。

这是很有意思的一个交叉验证：
    espeak 合成 "sheep" -> 音素模型应该听出 /iː/
    espeak 合成 "ship"  -> 音素模型应该听出 /ɪ/
如果两者能分开，说明「最小对立对」训练用的音频是可靠的。
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.core import phonemes as P     # noqa: E402
from videonote.core import tts                # noqa: E402

FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def main() -> int:
    print("=== 1. TTS 可用性 ===")
    check("espeak 合成可用", tts.available())
    if not tts.available():
        return 1

    print("\n=== 2. 合成基本功能 ===")
    a = tts.synth("ship")
    check("能合成出样本", a is not None and len(a) > 1000,
          f"{len(a) if a is not None else 0} 个样本 @ {tts._sample_rate()}Hz "
          f"({(len(a) / tts._sample_rate() if a is not None else 0):.2f} 秒)")
    p = tts.ensure_word("think")
    check("落盘成 wav 文件", p is not None and p.exists(),
          f"{p.name if p else '?'} {p.stat().st_size // 1024 if p and p.exists() else 0} KB")
    if p and p.exists():
        with wave.open(str(p), "rb") as f:
            check("wav 参数正确（16k 单声道…不，22050 单声道 16bit）",
                  f.getnchannels() == 1 and f.getsampwidth() == 2,
                  f"{f.getnchannels()} 声道 / {f.getframerate()}Hz / {f.getsampwidth() * 8}bit")
    # 缓存：第二次合成应该走文件
    b = tts.synth("ship")
    check("第二次走缓存且结果一致", b is not None and len(b) == len(a))

    if not P.model_ready():
        print("\n⏳ 音素模型没就绪，跳过交叉验证")
        return 1 if FAIL else 0

    print("\n=== 3. 交叉验证：合成音 -> 音素模型听回来 ===")
    print("   说明：音素模型是**真人语音**训练的，对 espeak 的机器音属于域外数据。")
    print("   下面这些是实测能听对的；听不对的由 tools/verify_pairs.py 过滤掉。")
    print("   （典型反例：机器音的 /θ/ 会被听成 /s/，所以 think/sink 那对没收录。）")
    cases = [
        ("feet", "iː"), ("fit", "ɪ"),
        ("vine", "v"), ("wine", "w"),
        ("right", "ɹ"), ("light", "l"),
        ("thing", "ŋ"), ("thin", "n"),
        ("ship", "ʃ"), ("sip", "s"),
    ]
    heard_map = {}
    for word, expect in cases:
        wav = tts.synth(word)
        if wav is None:
            check(f"{word} 合成", False)
            continue
        wav16 = tts.resample(wav, tts._sample_rate(), 16000)
        res = P.assess_audio(wav16, P.word_to_phones(word))
        if not res.get("ok"):
            check(f"{word} 评估", False, str(res.get("reason")))
            continue
        heard = res["heard"]
        heard_map[word] = heard
        got = expect in heard
        check(f"{word:8} 期望含 /{expect}/  模型听到 [{' '.join(heard)}]",
              got, f"准确率 {res['accuracy']}%")

    print("\n=== 4. 验证机制本身在工作 ===")
    from videonote.core import minimal_pairs as MP
    v = MP.load_verified()
    if not v:
        check("验证结果文件存在（先跑 tools/verify_pairs.py）", False)
    else:
        passed = [k for k, x in v.items() if x.get("ok")]
        check("验证文件里有合格词对", len(passed) > 0,
              f"{len(passed)}/{len(v)} 对通过")
        check("听音辨词只会拿到合格的那些",
              len(MP.all_pairs(only_verified=True)) == len(passed),
              f"{len(MP.all_pairs(only_verified=True))} 对可用")
        check("跟读对比能拿到全部词对（判定依据是 IPA，不是合成音）",
              len(MP.all_pairs(only_verified=False)) == len(MP.PAIRS),
              f"{len(MP.PAIRS)} 对")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ TTS + 音素交叉验证全部通过")
    return 1 if FAIL else 0


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = int(len(x) * sr_out / sr_in)
    idx = np.linspace(0, len(x) - 1, n_out)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


if __name__ == "__main__":
    raise SystemExit(main())
