# -*- coding: utf-8 -*-
"""启动入口：python -m videonote [--port 8756] [--no-browser] [--reload]"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
import webbrowser

from .config import settings


def _fix_temp_dir() -> None:
    """这台机器上 TEMP/TMP 没设置，Python 会把临时文件丢到「当前目录」。

    上传大文件时 Starlette 会先把请求体写进临时文件，落到项目根目录会很难看，
    也容易在权限受限的机器上失败。统一指到 data/_tmp。
    """
    tmp = settings.data_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP"):
        if not os.environ.get(name):
            os.environ[name] = str(tmp)
    tempfile.tempdir = str(tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="videonote", description="VideoNote 视频学习工作台")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="改代码自动重启（开发用）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--preload-model", action="store_true", help="启动时就把 Whisper 模型加载进内存")
    args = parser.parse_args(argv)

    _fix_temp_dir()

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        def open_later() -> None:
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=open_later, daemon=True).start()

    print(f"""
  ┌──────────────────────────────────────────────┐
  │  VideoNote 视频学习工作台                    │
  │  {url:<44}│
  │  数据目录: {str(settings.data_dir):<34}│
  └──────────────────────────────────────────────┘
  提示：大模型 key 填在 .env；没有 key 也能跑（本地摘要 + 本地语音识别）
""")

    if args.preload_model:
        threading.Thread(target=_preload, daemon=True).start()

    import uvicorn

    uvicorn.run("videonote.api.app:app", host=args.host, port=args.port, reload=args.reload,
                log_level="info")
    return 0


def _preload() -> None:
    try:
        from .providers.base import get_asr_provider

        provider = get_asr_provider("local")
        print(f"[videonote] 预加载语音模型: {provider.describe()}")
        import faster_whisper  # noqa: F401

        from .providers.asr_local import load_model

        load_model()
        print("[videonote] 语音模型已就绪")
    except Exception as exc:  # noqa: BLE001
        print(f"[videonote] 预加载失败（不影响使用，首次识别时会再试）: {exc}")


if __name__ == "__main__":
    sys.exit(main())
