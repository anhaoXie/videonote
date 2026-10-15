# -*- coding: utf-8 -*-
"""下载音素识别模型（wav2vec2 espeak 音素模型，约 1.2GB，只需一次）。

用法：
    .venv\\Scripts\\python.exe tools\\get_phoneme_model.py
    .venv\\Scripts\\python.exe tools\\get_phoneme_model.py --repo facebook/wav2vec2-xlsr-53-espeak-cv-ft

为什么不用 huggingface_hub 的 snapshot_download：
    这台机器上 pip/hub 的网络路径偶尔会卡死（走代理时零进展），
    直接用 urllib 分块下载 + 进度显示最稳，而且能自己校验文件大小。
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from videonote.config import settings  # noqa: E402

FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.json",
    "pytorch_model.bin",
]

# 已知大小（字节），只用于提示，不做强校验
EXPECT = {"pytorch_model.bin": 1263535127}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fetch(url: str, dst: Path, label: str) -> None:
    tmp = dst.with_suffix(dst.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "videonote/0.3"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            now = time.time()
            if now - last > 3:
                last = now
                speed = got / max(0.1, now - t0)
                pct = f"{got / total * 100:5.1f}%" if total else "  ?  "
                print(f"    {label}: {pct}  {human(got)} / {human(total) or '?'}  {human(speed)}/s",
                      flush=True)
    if total and tmp.stat().st_size != total:
        raise RuntimeError(f"{label} 下载不完整：{tmp.stat().st_size} != {total}")
    tmp.replace(dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=settings.phoneme_model)
    ap.add_argument("--dir", default="")
    args = ap.parse_args()

    dst = Path(args.dir) if args.dir else settings.models_dir / settings.phoneme_model.split("/")[-1]
    dst.mkdir(parents=True, exist_ok=True)
    base = (settings.hf_endpoint or "https://huggingface.co").rstrip("/")
    print(f"模型仓库: {args.repo}")
    print(f"镜像站点: {base}")
    print(f"保存目录: {dst}\n")

    failures = []
    for name in FILES:
        target = dst / name
        if target.exists() and target.stat().st_size > 0:
            print(f"  ✓ {name} 已存在（{human(target.stat().st_size)}）")
            continue
        url = f"{base}/{args.repo}/resolve/main/{name}"
        print(f"  ↓ {name}")
        try:
            fetch(url, target, name)
            print(f"  ✓ {name} 完成（{human(target.stat().st_size)}）")
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            failures.append((name, str(exc)))
            print(f"  ✗ {name} 失败：{exc}")

    print()
    if failures:
        print("以下文件没下成功，重跑本脚本即可续传：")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1
    print(f"✅ 模型就绪：{dst}")
    print("   现在「跟读评分」会自动带上音素级分析（哪个音发错了）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
