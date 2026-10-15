# -*- coding: utf-8 -*-
"""生成 VideoNote 的图标（web/icon.png 和 web/icon.ico）。

    .venv\\Scripts\\python.exe tools\\make_icon.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PNG = ROOT / "web" / "icon.png"
OUT_ICO = ROOT / "web" / "icon.ico"

BG = (11, 13, 18, 255)          # 深色底
ACCENT = (79, 140, 255, 255)    # 蓝色播放键
GREEN = (34, 197, 94, 255)      # 绿色字幕线
MUTED = (139, 147, 167, 255)


def draw(size: int):
    from PIL import Image, ImageDraw

    s = size * 4                      # 4 倍超采样，缩小后边缘才平滑
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=BG)

    # 播放三角（居中偏上，留足呼吸空间）
    cx, cy = s * 0.50, s * 0.40
    t = s * 0.155
    d.polygon([(cx - t * 0.72, cy - t), (cx - t * 0.72, cy + t), (cx + t * 0.92, cy)], fill=ACCENT)

    # 字幕线（两行，下面一行短一点，像字幕）
    w = s * 0.56
    x0 = s * 0.22
    y1 = s * 0.68
    d.rounded_rectangle([x0, y1, x0 + w, y1 + s * 0.07], radius=s * 0.035, fill=GREEN)
    y2 = s * 0.80
    d.rounded_rectangle([x0, y2, x0 + w * 0.62, y2 + s * 0.07], radius=s * 0.035, fill=MUTED)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = [draw(n) for n in sizes]
    imgs[0].save(OUT_PNG)
    imgs[0].save(OUT_ICO, sizes=[(n, n) for n in sizes])
    print(f"已生成 {OUT_PNG.name} 和 {OUT_ICO.name}（{', '.join(str(n) for n in sizes)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
