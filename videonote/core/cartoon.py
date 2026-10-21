# -*- coding: utf-8 -*-
"""2D 卡通讲解员：纯程序绘制 + 程序动画，**不需要用户上传任何照片**。

为什么不再用 Wav2Lip（照片级口型）：
  1. 它必须有真人照片。用户不上传就没有头像 —— 产品最关键的入口不能留空。
  2. 96×96 的嘴部生成器贴回大图后，糊且「半真半假」，比没有更糟（实测成片已确认）。
  3. 网上随便找一张卡通图放进公开仓库有版权风险，而且**平面图不会眨眼**。

这里的角色是代码画出来的：
  - 4 倍超采样再缩小 -> 边缘干净（Pillow 画圆会有锯齿，必须超采样）
  - 眨眼：每隔 3~5 秒闭一次（120ms）
  - 对口型：按配音的逐帧能量开合嘴巴（不是音素级，但观感自然）
  - 呼吸：整体上下浮动 2~3px；头部轻微摆动
  - 外观：深色圆形底 + 简洁人像，合成到视频右下角就是「讲师小窗」

对外接口：
  audio_envelope(wav, fps)       -> 每帧归一化能量 [0,1]
  draw_presenter(t, mouth, blink, sway, size) -> RGBA Image
  render_frames(wav, out_dir, ...) -> 帧数与 fps
  apply_to_video(video_in, video_out, fps, size, corner) -> 把讲解员叠到成片上
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

# 配色（深色主题，和课程画面一致）
BG = (16, 19, 26)
RING = (46, 54, 70)
SKIN = (243, 206, 172)
SKIN_SHADE = (226, 185, 150)
HAIR = (43, 45, 56)
BLAZER = (58, 82, 132)
BLAZER_DARK = (45, 64, 104)
SHIRT = (238, 240, 245)
EYE = (32, 34, 42)
BROW = (54, 50, 58)
MOUTH_IN = (94, 52, 52)
LIP = (196, 122, 116)

SS = 4  # 超采样倍数


def audio_envelope(wav_path: Path, fps: int = 25) -> List[float]:
    """把配音切成每帧一个能量值（归一化到 0~1）。"""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(wav_path), always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    data = np.asarray(data, dtype="float32")

    win = max(1, int(sr / fps))
    n = max(1, len(data) // win)
    env = np.zeros(n, dtype="float32")
    for i in range(n):
        chunk = data[i * win:(i + 1) * win]
        env[i] = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0

    peak = float(np.percentile(env, 95)) or 1.0
    env = np.clip(env / peak, 0.0, 1.0)
    # 轻微平滑，避免嘴部抖成噪声
    if n >= 3:
        env = np.convolve(env, np.array([0.25, 0.5, 0.25]), mode="same")
    return [float(v) for v in env]


def _blink_at(t: float, seed: float = 0.0) -> float:
    """返回 1=完全闭眼，0=睁开。约每 4 秒眨一次。"""
    period = 3.6 + (seed % 1.0) * 1.4
    phase = (t + seed * 3.0) % period
    if phase < 0.12:
        # 0->1->0 的三角形
        return 1.0 - abs(phase / 0.12 * 2.0 - 1.0)
    return 0.0


def draw_presenter(t: float, mouth: float, blink: float = 0.0,
                   sway: float = 0.0, size: int = 512) -> Image.Image:
    """画某一帧的讲解员（RGBA，size×size）。mouth/blink/sway 都是 0~1。

    设计取舍（重要）：刻意**不用写实五官**。写实脸最容易掉进「恐怖谷」——
    这版是扁平风格：眼睛用实心点、嘴型简单，观感是「卡通讲师」而不是「半真半假的人」。
    """
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = S / 512.0  # 以 512 为设计基准

    def r(v: float) -> float:
        return v * k

    cx = S / 2 + sway * r(3.0)

    # ---- 圆形底（顶部略亮，避免死黑）----
    d.ellipse([r(8), r(8), S - r(8), S - r(8)], fill=BG + (255,))
    for i in range(10):
        rr = 1.0 - i * 0.09
        d.ellipse([S / 2 - (S / 2 - r(8)) * rr, S / 2 - (S / 2 - r(8)) * rr,
                   S / 2 + (S / 2 - r(8)) * rr, S / 2 + (S / 2 - r(8)) * rr],
                  fill=(20 + i, 24 + i, 32 + i, 255))
    d.ellipse([r(8), r(8), S - r(8), S - r(8)], outline=RING + (255,), width=max(1, int(r(2))))

    # 人像画在独立图层，最后用圆形蒙版裁掉溢出
    # （否则肩膀是圆角矩形，会盖住圆边、露出一个方角）
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 几何量（512 基准）
    head_cx = cx
    head_cy = r(210)
    head_rx, head_ry = r(104), r(112)
    head_bottom = head_cy + head_ry
    body_top = r(352)

    # ---- 脖子（在头下方，被身体压住一部分）----
    d.rounded_rectangle([head_cx - r(30), head_cy + r(70), head_cx + r(30), body_top + r(24)],
                        radius=r(16), fill=SKIN_SHADE + (255,))

    # ---- 耳朵（画在头之前，会被头挡住一半）----
    for sx in (-1, 1):
        ex = head_cx + sx * head_rx
        d.ellipse([ex - r(14), head_cy - r(6), ex + r(14), head_cy + r(34)],
                  fill=SKIN_SHADE + (255,))

    # ---- 头 ----
    d.ellipse([head_cx - head_rx, head_cy - head_ry,
               head_cx + head_rx, head_cy + head_ry], fill=SKIN + (255,))

    # ---- 头发（盖住头顶；下缘必须停在眉毛之上，否则会盖住眼睛）----
    d.ellipse([head_cx - head_rx - r(12), head_cy - head_ry - r(9),
               head_cx + head_rx + r(12), head_cy - r(48)], fill=HAIR + (255,))
    # 额前一小撮刘海，避免「戴帽子」感
    d.ellipse([head_cx - r(44), head_cy - head_ry - r(6),
               head_cx + r(18), head_cy - head_ry + r(40)], fill=HAIR + (255,))

    # ---- 五官 ----
    eye_y = head_cy - r(16)
    eye_dx = r(40)
    eye_rx, eye_ry = r(12), r(14)

    for sx in (-1, 1):
        ex = head_cx + sx * eye_dx
        if blink > 0.5:
            d.arc([ex - eye_rx, eye_y - r(6), ex + eye_rx, eye_y + r(8)],
                  start=200, end=340, fill=EYE + (255,), width=max(2, int(r(4))))
        else:
            h = eye_ry * (1.0 - blink * 0.75)
            d.ellipse([ex - eye_rx, eye_y - h, ex + eye_rx, eye_y + h], fill=EYE + (255,))
            # 一点高光，避免死鱼眼
            d.ellipse([ex - eye_rx * 0.45, eye_y - h * 0.75,
                       ex - eye_rx * 0.05, eye_y - h * 0.2], fill=(245, 247, 250, 210))
        # 眉毛（细、平缓，别太凶）
        by = eye_y - r(30)
        d.line([(ex - r(15), by + r(2)), (ex + r(15), by)], 
               fill=BROW + (255,), width=max(2, int(r(3))))

    # 鼻子：一个很小的弧
    d.arc([head_cx - r(8), head_cy + r(26), head_cx + r(8), head_cy + r(42)],
          start=200, end=340, fill=SKIN_SHADE + (255,), width=max(1, int(r(3))))

    # ---- 嘴 ----
    mw = r(26)
    open_h = r(26) * max(0.0, min(1.0, mouth))
    my = head_cy + r(58)
    if open_h < r(5):
        d.arc([head_cx - mw, my - r(10), head_cx + mw, my + r(8)],
              start=20, end=160, fill=(150, 96, 92) + (255,), width=max(2, int(r(3))))
    else:
        d.rounded_rectangle([head_cx - mw, my - open_h / 2, head_cx + mw, my + open_h / 2],
                            radius=min(r(12), open_h / 2), fill=MOUTH_IN + (255,))
        # 下唇提亮，避免一个黑洞
        d.rounded_rectangle([head_cx - mw * 0.7, my + open_h * 0.15,
                             head_cx + mw * 0.7, my + open_h * 0.5],
                            radius=r(4), fill=(168, 92, 92) + (255,))

    # ---- 身体（压在脖子下缘上）----
    d.rounded_rectangle([cx - r(150), body_top, cx + r(150), S - r(6)],
                        radius=r(64), fill=BLAZER + (255,))
    # 领口
    d.polygon([(cx - r(34), body_top), (cx + r(34), body_top), (cx, body_top + r(58))],
              fill=SHIRT + (255,))
    d.polygon([(cx - r(15), body_top + r(2)), (cx + r(15), body_top + r(2)),
               (cx, body_top + r(34))], fill=BLAZER_DARK + (255,))

    # 用圆形蒙版贴回去，再补一圈细描边
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse([r(8), r(8), S - r(8), S - r(8)], fill=255)
    img.paste(layer, (0, 0), mask)
    ImageDraw.Draw(img).ellipse([r(8), r(8), S - r(8), S - r(8)],
                                outline=RING + (255,), width=max(1, int(r(2))))

    return img.resize((size, size), Image.LANCZOS)


def render_frames(wav_path: Path, out_dir: Path, fps: int = 25,
                  size: int = 384, limit_seconds: Optional[float] = None) -> int:
    """按配音生成一串带透明通道的 PNG 帧，返回帧数。"""
    import soundfile as sf

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    info = sf.info(str(wav_path))
    dur = float(info.duration)
    if limit_seconds:
        dur = min(dur, float(limit_seconds))

    env = audio_envelope(Path(wav_path), fps=fps)
    n = max(1, int(dur * fps))
    for i in range(n):
        t = i / fps
        mouth = env[i] if i < len(env) else 0.0
        breath = math.sin(2 * math.pi * t / 3.6) * 0.5 + 0.5     # 0..1
        sway = math.sin(2 * math.pi * t / 7.0)                   # -1..1
        frame = draw_presenter(t, mouth, blink=_blink_at(t),
                               sway=sway * 0.35, size=size)
        # 呼吸：整体平移 2px
        dy = int(round((breath - 0.5) * 4))
        if dy:
            shifted = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            shifted.paste(frame, (0, dy))
            frame = shifted
        frame.save(Path(out_dir) / f"{i:05d}.png")
    return n


def apply_to_video(video_in: Path, video_out: Path, fps: int = 25,
                   size: int = 384, margin: int = 36) -> Path:
    """把讲解员叠到成片右下角（用配音的能量驱动口型）。"""
    import imageio_ffmpeg

    video_in = Path(video_in)
    video_out = Path(video_out)
    work = video_in.parent / "_avatar"
    work.mkdir(parents=True, exist_ok=True)

    # 成片的音轨就是配音
    wav = work / "audio.wav"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-i", str(video_in), "-vn", "-ac", "1",
                    "-ar", "22050", str(wav)], capture_output=True)
    if not wav.exists():
        raise RuntimeError("取不到成片音轨，无法生成口型")

    n = render_frames(wav, work / "frames", fps=fps, size=size)
    if not n:
        raise RuntimeError("没有生成任何讲解员帧")

    cmd = [
        ff, "-y",
        "-i", str(video_in),
        "-framerate", str(fps), "-i", str(work / "frames" / "%05d.png"),
        "-filter_complex",
        f"[0:v][1:v]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]",
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        str(video_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=3600)
    if proc.returncode != 0 or not video_out.exists():
        raise RuntimeError(f"叠加讲解员失败：{(proc.stderr or '')[-600:]}")

    # 中间帧很占地方，成功即清
    for p in sorted((work / "frames").glob("*.png")):
        p.unlink(missing_ok=True)
    return video_out
