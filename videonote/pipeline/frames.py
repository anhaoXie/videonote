# -*- coding: utf-8 -*-
"""frames —— 图像处理：场景切换检测 + 关键帧抽取 +（可选）画面文字 OCR。"""
from __future__ import annotations

from typing import Any, Dict

from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..providers.base import get_vision_provider


@register_stage
class FramesStage(Stage):
    name = "frames"
    label = "画面分析"
    description = "按画面变化抽关键帧，识别屏幕上的文字/板书（PPT、字幕卡、代码）"
    requires = ("media_file",)
    produces = "frames"
    optional = True

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        media = ctx.media_path
        if not media:
            raise RuntimeError("没有媒体文件")
        vision = get_vision_provider(ctx.params.get("vision_provider"))
        threshold = float(ctx.params.get("scene_threshold", 0.35))
        max_frames = int(ctx.params.get("max_frames", 240))
        ocr = bool(ctx.params.get("ocr", False))
        out_dir = ctx.dir / "frames"

        scene_frames = getattr(vision, "scene_frames", None)
        if scene_frames is None:
            ctx.log("当前视觉 provider 不支持关键帧抽取，跳过", "warn")
            return {"frames": 0}

        frames = scene_frames(media, out_dir, threshold=threshold, max_frames=max_frames,
                              ocr=ocr, progress=ctx.set_progress)
        payload = [f for f in frames]
        ctx.save_json("frames", payload, "frames.json", meta={"count": len(payload)})
        # 生成一张联系表（缩略图墙），前端一眼看完
        try:
            _contact_sheet(frames, ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"拼接缩略图失败（不影响主流程）: {exc}", "warn")
        ctx.set_progress(100, f"关键帧 {len(frames)} 张")
        return {"frames": len(frames), "dir": str(out_dir)}


def _contact_sheet(frames: list, ctx: StageContext, cols: int = 6) -> None:
    from PIL import Image

    picked = frames[:60]
    if not picked:
        return
    thumbs = []
    for f in picked:
        try:
            im = Image.open(f["path"]).convert("RGB")
            im.thumbnail((240, 135))
            thumbs.append(im)
        except Exception:  # noqa: BLE001
            continue
    if not thumbs:
        return
    w, h = 240, 135
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (16, 18, 24))
    for i, im in enumerate(thumbs):
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    out = ctx.artifact_path("frames_sheet", "contact_sheet.jpg")
    sheet.save(out, quality=80)
    from .. import db

    db.put_artifact(ctx.project_id, "frames_sheet", str(out), kind="image", meta={"count": len(thumbs)})
