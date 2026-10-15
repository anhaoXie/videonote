# -*- coding: utf-8 -*-
"""captions —— 用视觉大模型（VLM）看懂画面，写进笔记。

为什么值得做：PPT、板书、代码、演示操作、画面里的文字，光靠语音转写是拿不到的。
把关键帧丢给视觉模型（硅基流动的 Qwen2.5-VL、GLM-4V 等都是 OpenAI 兼容接口），
让模型用一句中文说清这一帧在讲什么，再把结果并进笔记。

没配 key 时这个阶段是 optional，会跳过并在日志里说明。
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Any, Dict, List

from ..config import settings
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext


def _pick_frames(frames: List[dict], segments: List[dict], limit: int) -> List[dict]:
    """挑值得描述的帧：优先「画面有文字/变化大」的，并且附近有人在说话。"""
    if not frames:
        return []
    segs = segments or []

    def nearby_text(t: float) -> str:
        best, best_gap = "", 99.0
        for s in segs:
            mid = (float(s["start"]) + float(s["end"])) / 2
            gap = abs(mid - t)
            if gap < best_gap:
                best, best_gap = s.get("text", ""), gap
        return best if best_gap <= 12 else ""

    scored = []
    for f in frames:
        text = nearby_text(float(f.get("time", 0)))
        score = float(f.get("score", 0)) + (1.0 if (f.get("ocr_text") or "").strip() else 0)
        if text:
            score += 0.3
        scored.append((score, f, text))
    scored.sort(key=lambda x: -x[0])
    picked = scored[:limit]
    picked.sort(key=lambda x: float(x[1].get("time", 0)))
    return [{"frame": p[1], "speech": p[2]} for p in picked]


@register_stage
class CaptionStage(Stage):
    name = "captions"
    label = "画面描述"
    description = "把关键帧交给视觉大模型描述（板书/PPT/演示操作都能进笔记）"
    requires = ("frames",)
    produces = "frames_captioned"
    optional = True

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        frames = ctx.frames()
        if not frames:
            raise RuntimeError("没有关键帧，先跑画面分析")

        provider_name = (ctx.params.get("vision_provider") or settings.vision_provider or "vlm").lower()
        from ..providers.base import get_vision_provider

        vision = get_vision_provider(provider_name)
        # 允许任务参数临时指定接口（测试/多供应商切换时很有用）
        if provider_name == "vlm" and any(k in ctx.params for k in ("vlm_base_url", "vlm_api_key", "vlm_model")):
            from ..providers.vision_opencv import VLMVision

            vision = VLMVision(base_url=ctx.params.get("vlm_base_url"),
                               api_key=ctx.params.get("vlm_api_key"),
                               model=ctx.params.get("vlm_model"))
        if getattr(vision, "name", "") in ("none", "local_opencv"):
            ctx.log("没有配置视觉大模型（VN_VISION_PROVIDER=vlm + VN_VLM_API_KEY），跳过画面描述", "warn")
            return {"captioned": 0, "skipped": True}

        limit = int(ctx.params.get("max_frames") or settings.vlm_max_frames)
        picked = _pick_frames(frames, ctx.segments(), limit)
        if not picked:
            return {"captioned": 0}
        ctx.log(f"用 {provider_name} 描述 {len(picked)} 帧画面")

        done = 0

        def work(item: Dict[str, Any]) -> None:
            nonlocal done
            f = item["frame"]
            prompt = (
                "这是一段视频里的一个画面。"
                + (f'当时有人在说：「{item["speech"][:60]}」。' if item["speech"] else "")
                + "请用一句中文（不超过 40 字）描述画面里对理解内容有用的信息："
                  "比如屏幕上写的字、PPT 标题、代码、图表、演示的操作、人物动作或场景。"
                  "如果只是普通人物画面、没有额外信息，就回答「无」。"
            )
            cap = vision.caption(f["path"], prompt=prompt)
            cap = (cap or "").strip()
            if cap and cap != "无":
                f["caption"] = cap
            done += 1
            ctx.set_progress(done / len(picked) * 100.0, f"画面描述 {done}/{len(picked)}")

        # 串行 3 路并发，避免把接口打爆
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(work, picked))

        # 写回 frames.json（前端「关键帧」页直接就能看到描述）
        frames_path = ctx.artifact_file("frames")
        if frames_path:
            import json

            frames_path.write_text(json.dumps(frames, ensure_ascii=False, indent=2), encoding="utf-8")

        captioned = [f for f in frames if (f.get("caption") or "").strip()]
        ctx.save_json("frames_captioned", {"captioned": len(captioned), "engine": provider_name},
                      "captions_meta.json", meta={"captioned": len(captioned)})

        # 关键：画面描述变了，笔记就得重算。
        # 否则「已完成跳过」会让用户看到的笔记里没有画面信息（这个坑踩过一次）。
        from .. import db

        for key in ("notes", "summary"):
            try:
                db.delete_artifact(ctx.project_id, key)
            except Exception:  # noqa: BLE001
                pass
        ctx.log("已让摘要笔记失效，接下来会带着画面信息重新生成")

        ctx.set_progress(100, f"画面描述完成：{len(captioned)} 帧有内容")
        return {"captioned": len(captioned), "engine": provider_name}
