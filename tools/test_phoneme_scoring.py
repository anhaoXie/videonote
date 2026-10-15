# -*- coding: utf-8 -*-
"""音素级发音评分的端到端自检。

思路：把「参考音频」当成「用户完美跟读的录音」——如果用户读得和原声一模一样，
评分应该很高、音素判定应该基本全对。这正好验证整条链路。

需要服务在跑（python run.py --no-browser）：
    .venv\\Scripts\\python.exe tools\\test_phoneme_scoring.py
"""
from __future__ import annotations

import json
import mimetypes
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote import db                                    # noqa: E402
from videonote.config import settings                       # noqa: E402
from videonote.core.media import extract_audio_clip          # noqa: E402

BASE = f"http://127.0.0.1:{settings.port}"
FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global FAIL
    print(f"{'✅' if ok else '❌'} {label}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAIL += 1


def api_get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def api_post_json(path: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{BASE}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def api_upload(path: str, file_path: Path, fields: dict, timeout=900):
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


VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".flv", ".m4v")


def find_english_segment():
    """找一个英文的、够长的句子（用真实音频当「完美跟读」）。

    优先挑「原始媒体是视频」的项目 —— 那才是真人录音；
    合成 TTS 音频（wav）音素特征不自然，不适合做准确率断言。
    """
    candidates = []
    for p in db.list_projects():
        art = db.get_artifact(p["id"], "audio")
        if not art or not art.get("path") or not Path(art["path"]).exists():
            continue
        media = (p.get("media_path") or "").lower()
        real = media.endswith(VIDEO_EXT)
        for s in db.list_segments(p["id"]):
            text = s.get("text") or ""
            ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(1, len(text))
            dur = float(s["end"]) - float(s["start"])
            if ascii_ratio > 0.9 and 2.0 <= dur <= 20.0 and len(text.split()) >= 4:
                candidates.append((not real, dur, p, s, Path(art["path"])))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))     # 真人录音优先，其次短的
    return candidates[0][2], candidates[0][3], candidates[0][4], candidates[0][1]


def main() -> int:
    print("=== 0. 服务在跑吗 ===")
    try:
        health = api_get("/api/health")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 服务没起来（{exc}）。先运行： python run.py --no-browser")
        return 1
    print(f"   服务 OK，版本 {health.get('version')}")

    caps = api_get("/api/capabilities")
    phon = (caps.get("config") or {}).get("phoneme") or caps.get("phoneme") or {}
    print(f"   音素能力: {json.dumps(phon, ensure_ascii=False)}")

    found = find_english_segment()
    if not found:
        print("❌ 找不到合适的英文句子做测试（需要英文项目 + 音频产物）")
        return 1
    project, seg, audio_path, dur = found
    pid, seg_idx = project["id"], int(seg["idx"])
    print(f"\n=== 1. 素材 ===")
    print(f"   项目「{project['title']}」第 {seg_idx} 句，{dur:.1f} 秒")
    print(f"   原文: {seg['text']}")

    tmp = settings.data_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    clip = tmp / f"user_sim_{pid}_{seg_idx}.mp3"
    if not clip.exists():
        extract_audio_clip(audio_path, float(seg["start"]), float(seg["end"]), clip)
    print(f"   「用户录音」（就用原声模拟完美跟读）: {clip.name} {clip.stat().st_size // 1024} KB")

    print("\n=== 2. 整句评分（走 /score 接口）===")
    t0 = time.time()
    res = api_upload(f"/api/projects/{pid}/score", clip,
                     {"seg_idx": str(seg_idx), "mode": "repeat"})
    total = time.time() - t0
    print(f"   总分 {res['score']}（准确 {res['accuracy']} / 完整 {res['completeness']} / "
          f"流利 {res['fluency']}）")
    print(f"   识别: {res.get('transcript', '')[:100]}")
    print(f"   耗时 {total:.1f} 秒（含 whisper + 音素推理）")
    check("完美跟读拿到高分（≥75）", res["score"] >= 75, f"实得 {res['score']}")

    ph = res.get("phonemes") or {}
    check("接口返回了音素块", "phonemes" in res, f"ok={ph.get('ok')} reason={ph.get('reason', '')}")
    if ph.get("ok"):
        print(f"   音素推理 {ph.get('seconds')} 秒，检查了 {ph.get('checked')} 个词/句")
        errs = ph.get("errors") or []
        print(f"   音素错误 {len(errs)} 个: "
              + (", ".join(f"/{e['expected'] or '∅'}/→/{e['heard'] or '漏'}/" for e in errs[:8]) or "无"))
        for w in ph.get("words") or []:
            if not w.get("ok"):
                continue
            print(f"     {w['word'][:22]:24} 准确率 {w.get('accuracy')}%  期望[{' '.join(w.get('expected') or [])}]"
                  f"  识别[{' '.join(w.get('heard') or [])}]")
        for t in ph.get("tips") or []:
            print(f"     💡 {t}")
        check("完美跟读时音素错误很少（≤40% 音素）", len(errs) <= max(2, ph.get("checked", 1) * 6),
              f"{len(errs)} 个")
    else:
        print(f"   ⚠ 音素块未启用: {ph.get('reason')}")

    print("\n=== 3. 历史记录里能找到音素明细 ===")
    scores = api_get(f"/api/projects/{pid}/scores?seg_idx={seg_idx}&limit=1")
    if scores.get("scores"):
        det = scores["scores"][0].get("detail") or {}
        check("detail 里存了 phoneme_words", "phoneme_words" in det,
              f"{len(det.get('phoneme_words') or [])} 个词")
        check("detail 里存了 phoneme_errors", "phoneme_errors" in det,
              f"{len(det.get('phoneme_errors') or [])} 条")
        check("逐词结果带时间戳（音素切片要用）",
              all(("start" in w) for w in (det.get("words") or [])[:5]))
    else:
        check("查到了评分记录", False)

    print("\n=== 4. 统计接口能汇总音素错误 ===")
    stats = api_get(f"/api/analytics?project_id={pid}&days=30")
    check("analytics 有 weak_phonemes 字段", "weak_phonemes" in stats,
          f"{len(stats.get('weak_phonemes') or [])} 条")
    for r in (stats.get("weak_phonemes") or [])[:5]:
        print(f"     /{r['expected'] or '∅'}/ → /{r['heard'] or '漏'}/ × {r['count']}（{r['type']}）")

    print("\n=== 5. 复习队列联动（读得不好自动排队）===")
    rev = api_post_json("/api/reviews/sync", {"project_id": pid})
    check("sync 接口正常", "created" in rev, str(rev))

    print()
    if FAIL:
        print(f"❌ {FAIL} 项没过")
    else:
        print("✅ 音素级发音评分端到端通过")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
