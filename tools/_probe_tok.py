# -*- coding: utf-8 -*-
"""排查音素模型的 tokenizer 为什么读不出词表。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.core import phonemes as P  # noqa: E402

d = P.model_dir()
print("目录:", d)
for f in sorted(d.iterdir()):
    print(f"  {f.name}  {f.stat().st_size:,} bytes")

tc = d / "tokenizer_config.json"
if tc.exists():
    print("\ntokenizer_config.json:", json.loads(tc.read_text(encoding="utf-8")))
vocab = json.loads((d / "vocab.json").read_text(encoding="utf-8"))
print("\nvocab.json 条目数:", len(vocab))
print("前 12 个:", list(vocab.items())[:12])
ips = [k for k in vocab if k not in ("<pad>", "<s>", "</s>", "<unk>")]
print("音素类 token 数:", len(ips))
print("样例:", ips[:30])

print("\n--- 试各种加载方式 ---")
try:
    from transformers import AutoTokenizer
    t = AutoTokenizer.from_pretrained(str(d))
    print("AutoTokenizer OK:", type(t).__name__, "vocab", len(t.get_vocab()), "pad", t.pad_token_id)
except Exception as exc:  # noqa: BLE001
    print("AutoTokenizer 失败:", type(exc).__name__, exc)
try:
    from transformers import Wav2Vec2CTCTokenizer
    t2 = Wav2Vec2CTCTokenizer.from_pretrained(str(d))
    print("Wav2Vec2CTCTokenizer OK: vocab", len(t2.get_vocab()), "pad", t2.pad_token_id)
except Exception as exc:  # noqa: BLE001
    print("Wav2Vec2CTCTokenizer 失败:", type(exc).__name__, exc)
try:
    from transformers import Wav2Vec2CTCTokenizer
    t3 = Wav2Vec2CTCTokenizer(str(d / "vocab.json"), unk_token="<unk>",
                              pad_token="<pad>", word_delimiter_token="|")
    print("直接读 vocab.json OK: vocab", len(t3.get_vocab()), "pad", t3.pad_token_id)
except Exception as exc:  # noqa: BLE001
    print("直接读 vocab.json 失败:", type(exc).__name__, exc)
