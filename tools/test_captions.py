# -*- coding: utf-8 -*-
"""⑤ 画面描述（视觉大模型）自检 —— 不需要真的 API key。

    .venv\\Scripts\\python.exe tools\\test_captions.py

它会自己起一个假的视觉模型服务（tools/mock_vlm.py），
让 VideoNote 把关键帧发过去，检查描述有没有写回关键帧和笔记。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8756"
MOCK = "http://127.0.0.1:8799/v1"


def wait_job(c: httpx.Client, jid: str, timeout: float = 900) -> dict:
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
        time.sleep(1.5)


def start_mock() -> subprocess.Popen:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen([str(py), str(ROOT / "tools" / "mock_vlm.py")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    for _ in range(30):
        try:
            if httpx.get(f"{MOCK}/models", timeout=2).status_code == 200:
                print("假视觉模型服务已就绪:", MOCK)
                return proc
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("假视觉模型服务起不来")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=900)
    projects = c.get("/api/projects").json()["projects"]
    # 挑一个有关键帧的项目
    pid = None
    for p in projects:
        if "frames" in (p.get("artifact_keys") or []):
            pid = p["id"]
            break
    if not pid:
        print("没有找到有关键帧的项目，先跑一次「完整分析」")
        return 1
    print("使用项目:", pid)

    before = c.get(f"/api/projects/{pid}/frames").json()
    print(f"当前关键帧 {before['total']} 张，已带描述 {sum(1 for f in before['frames'] if f.get('caption'))} 张")

    mock = start_mock()
    try:
        resp = c.post(f"/api/projects/{pid}/jobs", json={
            "kind": "captions",
            "params": {
                "force_all": True,
                "vision_provider": "vlm",
                "vlm_base_url": MOCK,
                "vlm_api_key": "mock",
                "vlm_model": "mock-vl",
            },
        })
        if resp.status_code != 200:
            print("发起任务失败:", resp.status_code, resp.text[:300])
            return 1
        j = wait_job(c, resp.json()["job"]["id"])
        if j["status"] != "done":
            print("失败日志:")
            print("\n".join(l["message"] for l in c.get(f"/api/jobs/{j['id']}").json()["logs"][-8:]))
            return 1

        after = c.get(f"/api/projects/{pid}/frames").json()
        captioned = [f for f in after["frames"] if (f.get("caption") or "").strip()]
        print(f'\n关键帧描述结果：{len(captioned)}/{after["total"]} 张有描述')
        for f in captioned[:6]:
            print(f'  [{f["time"]:7.2f}s] {f["caption"]}')

        # 笔记里也应该出现「画面信息」
        c.post(f"/api/projects/{pid}/jobs", json={"kind": "summarize", "params": {"force": ["notes"]}})
        time.sleep(8)
        notes = c.get(f"/api/projects/{pid}/notes").json()["markdown"]
        has_section = "画面信息" in notes
        print("\n笔记里包含「画面信息」章节:", "✅" if has_section else "❌")
        if has_section:
            idx = notes.index("画面信息")
            print(notes[idx:idx + 220])

        if captioned and has_section:
            print("\n✅ 画面描述全链路通过（假视觉模型）")
            return 0
        print("\n⚠️ 没有完全通过")
        return 2
    finally:
        mock.terminate()
        try:
            out = mock.stdout.read() if mock.stdout else ""
            lines = [l for l in (out or "").splitlines() if "mock-vlm" in l]
            print(f"\n假服务被调用 {len(lines)} 次：")
            print("\n".join(lines[:6]))
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
