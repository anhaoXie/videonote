# -*- coding: utf-8 -*-
"""单词级训练（最小对立对）自检。

需要服务在跑： python run.py --no-browser
    .venv\\Scripts\\python.exe tools\\test_words.py
"""
from __future__ import annotations

import json
import mimetypes
import sys
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.config import settings       # noqa: E402
from videonote.core import tts              # noqa: E402

BASE = f"http://127.0.0.1:{settings.port}"
FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def get(path: str, raw: bool = False):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        data = r.read()
        return data if raw else json.loads(data.decode("utf-8"))


def post_form(path: str, fields: dict):
    body = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in fields.items()).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def post_file(path: str, file_path: Path, fields: dict, timeout=300):
    boundary = f"----vn{uuid.uuid4().hex}"
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{file_path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    body += file_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{BASE}{path}", data=bytes(body), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


import urllib.parse  # noqa: E402


def make_word_wav(word: str, out: Path) -> bool:
    """用 espeak 合成一个词，存成 22050Hz 单声道 wav（服务端会用 ffmpeg 解码）。"""
    a = tts.synth(word)
    if a is None:
        return False
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(tts._sample_rate())
        f.writeframes(pcm.tobytes())
    return True


def main() -> int:
    print("=== 0. 服务 ===")
    try:
        h = get("/api/health")
        print(f"   OK 版本 {h.get('version')}")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 服务没起来（{exc}）")
        return 1

    print("\n=== 1. 词库接口 ===")
    data = get("/api/words/pairs")
    check("返回词对清单", isinstance(data.get("pairs"), list) and len(data["pairs"]) > 0,
          f"共 {data.get('total')} 对，其中音频已验证 {data.get('verified_total')} 对")
    check("TTS 可用标记正确", "tts_available" in data, f"tts_available={data.get('tts_available')}")
    v = get("/api/words/pairs?verified_only=true")
    check("可以只取「验证过」的词对（听音辨词用）",
          0 < len(v["pairs"]) <= len(data["pairs"]), f"{len(v['pairs'])} 对")
    groups = get("/api/words/groups")["groups"]
    check("分组清单可用", len(groups) > 3, f"{len(groups)} 组")
    for g in groups[:4]:
        print(f"     {g['name']:16} {g['count']} 对（已验证 {g['verified']}，练过 {g['practiced']}）")

    print("\n=== 2. 合成音频 ===")
    wav = get("/api/words/audio?word=think", raw=True)
    check("能拿到 wav", len(wav) > 2000 and wav[:4] == b"RIFF", f"{len(wav)} bytes")
    slow = get("/api/words/audio?word=think&slow=true", raw=True)
    check("慢速版本更长", len(slow) > len(wav), f"{len(wav)} -> {len(slow)} bytes")

    print("\n=== 3. 个性化计划 ===")
    plan = get("/api/words/plan?limit=8")
    check("返回计划", isinstance(plan.get("plan"), list) and len(plan["plan"]) > 0,
          f"{len(plan['plan'])} 项")
    for p in plan["plan"][:4]:
        print(f"     {p['a']:10}/{p['b']:10} ({p['pa']}/{p['pb']})  {p['why']}"
              f"  音频{'有' if p.get('has_audio') else '仅文本'}")

    print("\n=== 4. 听音辨词记账 ===")
    pid = plan["plan"][0]["id"]
    r = post_form("/api/words/listen", {"pair_id": pid, "chosen": "ship", "answer": "ship", "seconds": 3})
    check("答对能记账", r.get("ok") and r.get("correct") is True)
    r2 = post_form("/api/words/listen", {"pair_id": pid, "chosen": "ship", "answer": "sheep", "seconds": 3})
    check("答错也记账且标记 incorrect", r2.get("ok") and r2.get("correct") is False)

    print("\n=== 5. 跟读一个字 -> 音素判定 ===")
    tmp = settings.data_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "word_attempt_think.wav"
    if not make_word_wav("think", f):
        check("合成测试录音", False, "espeak 合成失败")
    else:
        pair = next((x for x in data["pairs"] if x["a"] == "think"), None)
        if not pair:
            check("词库里有 think/sink", False)
        else:
            res = post_file("/api/words/say", f, {"pair_id": pair["id"], "word": "think"})
            check("接口返回完整结果",
                  res.get("ok") and res.get("phones") and res.get("expected"))
            print(f"     word={res.get('word')}  关键音 /{res.get('key_phone')}/  "
                  f"读对={res.get('key_hit')}  准确率={res.get('accuracy')}%  "
                  f"耗时 {res.get('seconds')}s")
            print(f"     期望[{' '.join(res.get('expected') or [])}]  "
                  f"听到[{' '.join(res.get('heard') or [])}]")
            for ph in (res.get("phones") or [])[:6]:
                print(f"        /{ph['p']}/ {ph['status']:5} 听过={ph['heard'] or '-'} GOP={ph['gop']}")
            for t in res.get("tips") or []:
                print(f"     💡 {t}")
            check("每个音素都有判定", all(p.get("status") for p in res["phones"]))

    print("\n=== 6. 练习记录可查 ===")
    stats = db_stats()
    check("按词对统计有数据", bool(stats), f"{len(stats)} 个词对有记录")
    for pid2, s in list(stats.items())[:3]:
        bits = "  ".join(f"{k}: {v['ok']}/{v['n']} 次对（均分 {v['avg_score']}）" for k, v in s.items())
        print(f"     {pid2:12} {bits}")

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ 单词级最小对立对训练全部通过")
    return 1 if FAIL else 0


def db_stats() -> dict:
    from videonote import db
    db.init()
    return db.pair_drill_stats()


if __name__ == "__main__":
    raise SystemExit(main())
