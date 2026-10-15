# -*- coding: utf-8 -*-
"""看一眼系统当前状态：插件、任务类型、每个项目的产出/句子/说话人/句库/评分。

    .venv\\Scripts\\python.exe tools\\status.py
"""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8756"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)
    try:
        health = c.get("/api/health").json()
    except Exception as exc:  # noqa: BLE001
        print(f"服务没起来（{exc}）。先运行 run.ps1")
        return 1
    print(f'服务: {"正常" if health.get("ok") else "异常"}  版本 {health.get("version")}  '
          f'worker {"忙" if health.get("worker_busy") else "空闲"}')

    cap = c.get("/api/capabilities").json()
    cfg = cap["config"]
    print(f'\n处理阶段 {len(cap["stages"])} 个: ' + ", ".join(s["name"] for s in cap["stages"]))
    print(f'任务类型 {len(cap["job_kinds"])} 种: ' + ", ".join(k["name"] for k in cap["job_kinds"]))
    for kind, names in cap["providers"].items():
        print(f'  {kind:<8} ' + ", ".join(names))
    errs = cap["plugins"]["import_errors"]
    if errs:
        print("  插件加载失败:", errs)
    print(f'\n语音识别: {cfg["asr"]["effective_provider"]} ({cfg["asr"]["local_model"]})   '
          f'大模型: {"已配置" if cfg["llm"]["ready"] else "未配置"}   '
          f'说话人分离: {cfg["diarize"]["provider"]}   '
          f'画面描述: {cfg["vision"]["provider"] or "未开启"}')

    projects = c.get("/api/projects").json()["projects"]
    print(f"\n项目 {len(projects)} 个:")
    for p in projects:
        d = c.get(f'/api/projects/{p["id"]}').json()
        segs = c.get(f'/api/projects/{p["id"]}/segments').json()["segments"]
        clips = c.get(f'/api/projects/{p["id"]}/clips').json()
        scores = c.get(f'/api/projects/{p["id"]}/scores').json()
        speakers = sorted({s["speaker"] for s in segs if s.get("speaker")})
        print(f'  {p["title"]}  [{p["id"]}]  时长 {p["duration"]:.0f}s')
        print(f'     句子 {len(segs)} | 说话人 {", ".join(speakers) or "-"} | '
              f'句库 {len(clips["clips"])} | 评分 {len(scores["scores"])} 次 | 产出 {len(d["artifacts"])} 项')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
