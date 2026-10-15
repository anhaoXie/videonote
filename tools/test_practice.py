# -*- coding: utf-8 -*-
"""跟读句库 / 发音评分 / 影子跟读 的自检脚本。

    .venv\\Scripts\\python.exe tools\\test_practice.py

用项目自带的音频当「用户录音」来验证：原样送进去应该接近满分，
截断/乱序送进去应该被扣分并指出漏词。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))     # 让脚本能 import videonote
BASE = "http://127.0.0.1:8756"


def show(title: str, obj) -> None:
    print(f"\n=== {title} ===")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False)[:1200])
    else:
        print(str(obj)[:1200])


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=600)
    pid = c.get("/api/projects").json()["projects"][0]["id"]
    print("project:", pid)

    # ---------- ① 句库 + Anki ----------
    r = c.post(f"/api/projects/{pid}/clips", json={"seg_idx": 0, "tags": "自检,跟读", "note": "测试句子"})
    clip = r.json()["clips"][0]
    audio = Path(clip["audio_path"])
    show("① 收藏句子", {"id": clip["id"], "audio": audio.name, "exists": audio.exists(),
                       "size": audio.stat().st_size if audio.exists() else 0})
    assert audio.exists(), "切片文件没有生成"

    lst = c.get(f"/api/projects/{pid}/clips").json()
    show("① 句库列表", {"count": len(lst["clips"]), "scores": lst["scores"]})

    exp = c.post(f"/api/projects/{pid}/clips/export", json={})
    show("① 导出 Anki", exp.json())
    deck = Path(exp.json()["path"])
    print("    牌组文件存在:", deck.exists(), f"{deck.stat().st_size/1024:.1f} KB" if deck.exists() else "")

    show("① 句子音频可在浏览器播放", c.get(f"/api/clips/{clip['id']}/media?kind=audio").status_code)

    # ---------- ② 发音评分 ----------
    raw = audio.read_bytes()
    name = audio.name
    r1 = c.post(f"/api/projects/{pid}/score", files={"file": (name, io.BytesIO(raw), "audio/mpeg")},
                data={"seg_idx": "0", "mode": "repeat"})
    s1 = r1.json()
    show("② 原声当跟读（应接近满分）", {"score": s1.get("score"), "accuracy": s1.get("accuracy"),
                                   "completeness": s1.get("completeness"), "fluency": s1.get("fluency"),
                                   "transcript": s1.get("transcript")})
    show("   逐词", [f'{d["w"]}:{d["status"]}' for d in (s1.get("detail") or {}).get("words", [])][:20])
    show("   建议", s1.get("advice"))

    # 截断版：只保留前 2.5 秒 -> 应该漏词 + 扣分
    from videonote.core.media import ffmpeg_exe
    import subprocess, tempfile

    tmp = ROOT / "data" / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    cut = tmp / "cut_test.mp3"
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-t", "2.5", "-i", str(audio), str(cut)], check=True)
    r2 = c.post(f"/api/projects/{pid}/score", files={"file": (cut.name, cut.read_bytes(), "audio/mpeg")},
                data={"seg_idx": "0", "mode": "repeat"})
    s2 = r2.json()
    show("② 截断版（应扣分并指出漏词）", {"score": s2.get("score"), "completeness": s2.get("completeness"),
                                     "missing": (s2.get("detail") or {}).get("missing"),
                                     "advice": s2.get("advice")})

    hist = c.get(f"/api/projects/{pid}/scores").json()
    show("② 评分历史", {"count": len(hist["scores"]), "best": hist["best"]})

    # ---------- ④ 影子跟读分析 ----------
    full = ROOT / "samples" / "sample_speech.wav"
    if full.exists():
        r3 = c.post(f"/api/projects/{pid}/shadow/analyze",
                    files={"file": (full.name, full.read_bytes(), "audio/wav")},
                    data={"start_seg": "0", "end_seg": "0", "video_start": "0"})
        d = r3.json()
        show("④ 影子跟读分析", {"coverage": d.get("coverage"), "lag": d.get("lag"),
                            "advice": d.get("advice"), "asr_seconds": d.get("asr_seconds")})
    else:
        print("\n=== ④ 影子跟读 跳过（没有 samples/sample_speech.wav）===")

    print("\n全部自检通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
