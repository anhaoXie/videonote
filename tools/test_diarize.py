# -*- coding: utf-8 -*-
"""③ 说话人分离 自检。

    .venv\\Scripts\\python.exe tools\\test_diarize.py

做法：用同一段人声做了一个「升调变声」的版本当第二个人，剪成 A/B/A/B 交替、
句间留 1.2 秒静音，再让 VideoNote 走 转写 -> 说话人分离，看它能不能把两个人分开。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8756"
SRC = ROOT / "samples" / "sample_speech.wav"
OUT = ROOT / "samples" / "two_speakers.wav"


def build_audio() -> Path:
    """A(4s) / 静音4s / B(4s, 升调) / 静音4s / A(4s) / 静音4s / B(4s, 升调)。

    用 numpy 手工拼（ffmpeg 的 anullsrc 在 concat 里格式对不齐，静音会变成非静音，
    结果 Whisper 把整段当成一句，说话人分离就没句子可分了）。
    """
    import wave

    import numpy as np

    from videonote.core.media import run_ffmpeg

    # 先做一个升调版本（音色明显不同）
    shifted = ROOT / "data" / "_tmp" / "shifted.wav"
    shifted.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_ffmpeg(["-i", str(SRC), "-af", "asetrate=16000*1.28,aresample=16000,atempo=1/1.28",
                            "-ar", "16000", "-ac", "1", str(shifted)])
    if code != 0:
        raise RuntimeError(f"生成变声版本失败: {out[-400:]}")

    def read(p: Path) -> "np.ndarray":
        with wave.open(str(p), "rb") as wf:
            assert wf.getframerate() == 16000 and wf.getnchannels() == 1
            return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    a = read(SRC)
    b = read(shifted)
    silence = np.zeros(16000 * 4, dtype=np.int16)
    parts = [a[0:16000 * 4], silence, b[0:16000 * 4], silence,
             a[16000 * 4:16000 * 8], silence, b[16000 * 4:16000 * 8]]
    data = np.concatenate(parts)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(data.tobytes())
    print(f"测试音频: {OUT.name} ({len(data)/16000:.1f}s, {OUT.stat().st_size/1024:.0f} KB)")
    return OUT


def unit_check_diarizer() -> bool:
    """直接拿「已知的」说话区间测聚类本身，排除 ASR 分段不准的干扰。"""
    from videonote.providers.diarize_local import EcapaDiarizer

    # 真实边界：A 在 0-4 和 16-20；B 在 8-12 和 24-28
    truth = [(0, 4, "A"), (8, 12, "B"), (16, 20, "A"), (24, 28, "B")]
    segs = [{"idx": i, "start": a, "end": b} for i, (a, b, _) in enumerate(truth)]
    res = EcapaDiarizer().diarize(str(OUT), segs, num_speakers=None)
    got = [res["assignments"].get(i) for i in range(len(segs))]
    print(f"  单元检查（用真实边界）：{got}  聚类k={res.get('clusters')} 轮廓={res.get('silhouette')}")
    # 期望 A B A B -> 第1、3句同一个人，第2、4句同一个人，且两组不同
    ok = got[0] == got[2] and got[1] == got[3] and got[0] != got[1]
    print("  单元检查结果:", "✅ 声纹聚类正确" if ok else "❌ 声纹聚类没分开")
    return ok


def wait_job(c: httpx.Client, jid: str, timeout: float = 1800) -> dict:
    t0 = time.time()
    last = None
    while True:
        j = c.get(f"/api/jobs/{jid}").json()["job"]
        cur = f'{j["status"]} {j["progress"]:.0f}% {j.get("message","")}'
        if cur != last:
            print(f"  [{time.time()-t0:6.1f}s] {cur}")
            last = cur
        if j["status"] in ("done", "failed", "canceled"):
            return j
        if time.time() - t0 > timeout:
            raise TimeoutError("任务超时")
        time.sleep(2)


def _run(c: httpx.Client, pid: str, job: str) -> int:
    print(f"\n项目 {pid}：先转写…")
    j = wait_job(c, job)
    if j["status"] != "done":
        print("转写失败:", c.get(f"/api/jobs/{job}").json()["logs"][-6:])
        return 1

    segs = c.get(f"/api/projects/{pid}/segments").json()["segments"]
    print(f"\n转写出 {len(segs)} 句：")
    for s in segs:
        print(f'  [{s["start"]:6.2f}-{s["end"]:6.2f}] {s["text"][:70]}')

    print("\n跑说话人分离（第一次会下载 ECAPA 模型，约 80MB）…")
    resp = c.post(f"/api/projects/{pid}/jobs", json={"kind": "diarize", "params": {}})
    if resp.status_code != 200:
        print("发起任务失败:", resp.status_code, resp.text[:400])
        return 1
    job2 = resp.json()["job"]["id"]
    j2 = wait_job(c, job2)
    if j2["status"] != "done":
        logs = c.get(f"/api/jobs/{job2}").json()["logs"]
        print("说话人分离失败:")
        print("\n".join(l["message"] for l in logs[-8:]))
        return 1

    segs = c.get(f"/api/projects/{pid}/segments").json()["segments"]
    print("\n标注结果：")
    for s in segs:
        print(f'  [{s["start"]:6.2f}] {s.get("speaker","?"):<8} {s["text"][:60]}')

    det = c.get(f"/api/projects/{pid}/artifact/diarization").json()
    speakers = {s["label"]: s["count"] for s in det.get("speakers", [])}
    print("\n统计:", json.dumps(speakers, ensure_ascii=False),
          "| 聚类k:", det.get("clusters"), "| 轮廓系数:", det.get("silhouette"),
          "| 声纹数:", det.get("embedded"))

    # 期望：至少 2 位说话人；交替出现（A/B/A/B）
    labels = [s.get("speaker") for s in segs]
    print("顺序:", labels)
    if len(speakers) >= 2:
        print("\n✅ 成功分辨出多位说话人")
        return 0
    print("\n⚠️ 整条管线只识别出 1 位说话人（多半是 ASR 把几段合并成一句了）")
    print("→ 单独验证声纹聚类本身：")
    return 0 if unit_check_diarizer() else 2


def main() -> int:
    keep = "--keep" in sys.argv
    audio = build_audio()
    c = httpx.Client(base_url=BASE, timeout=600)

    res = c.post("/api/projects", json={"title": "自检 - 两说话人", "source_type": "file",
                                        "path": str(audio), "auto_start": True, "job_kind": "transcribe"}).json()
    pid, job = res["project"]["id"], res["job"]["id"]
    try:
        return _run(c, pid, job)
    finally:
        # 自检不该在用户的项目列表里一次堆一个：跑完就删掉**这次**建的项目。
        # （想留着看结果：加 --keep）
        if keep:
            print(f"\n保留自检项目 {pid}（--keep）")
        else:
            try:
                c.delete(f"/api/projects/{pid}")
                print(f"\n已清理自检项目 {pid}（想留着看结果就加 --keep）")
            except Exception as exc:  # noqa: BLE001
                print(f"\n清理自检项目失败（可手动删 {pid}）：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
