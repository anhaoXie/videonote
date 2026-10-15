# -*- coding: utf-8 -*-
"""探一下某个视频有没有音频轨、多长、什么分辨率。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from videonote.core.media import probe  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python tools/probe_media.py <视频路径>")
        return 1
    p = Path(sys.argv[1])
    if not p.exists():
        print("文件不存在:", p)
        return 1
    info = probe(p)
    print(f"文件: {p.name}")
    print(f"  大小: {p.stat().st_size/1048576:.1f} MB")
    print(f"  时长: {info.duration:.1f} 秒 ({info.duration/60:.1f} 分钟)")
    print(f"  画面: {info.width}x{info.height} @ {info.fps:.1f} fps")
    print(f"  有音频轨: {'✅ 有' if info.has_audio else '❌ 没有（无法做语音识别）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
