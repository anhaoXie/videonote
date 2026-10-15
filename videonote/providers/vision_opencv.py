"""画面理解（图像处理）：

  local_opencv  —— 场景切换检测 + 关键帧抽取（+ 可选 OCR）。纯 CPU，零成本，默认启用。
  vlm           —— 把关键帧丢给 OpenAI 兼容的视觉模型（硅基流动 Qwen2.5-VL / GLM-4V 等）做画面描述。

关键帧的用途：
  * 记录板书/PPT/字幕卡，喂给摘要
  * 场景切分，给「自动分段 + 暂停跟读」提供更自然的断点
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from ..config import settings
from .base import VisionProvider
from ..core.registry import register_provider

_ocr_engine = None


def _get_ocr():
    """懒加载 OCR 引擎（需要 pip install rapidocr-onnxruntime）。"""
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        _ocr_engine = RapidOCR()
    return _ocr_engine


class OpenCVVision(VisionProvider):
    name = "local_opencv"

    def scene_frames(self, video: str | Path, out_dir: str | Path,
                     threshold: float = 0.35, max_frames: int = 240,
                     min_gap: float = 800.0, ocr: bool = False,
                     progress: Optional[Callable[[float, str], None]] = None) -> List[Dict[str, Any]]:
        """按画面变化抽关键帧。threshold 越小抽得越多。"""
        import cv2
        import numpy as np

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV 打不开视频: {video}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = total / fps if fps > 0 else 0
        # 采样步长：最多看 max_frames*fps 帧，避免长视频慢到离谱
        step = max(1, int(fps * 0.5))

        frames: List[Dict[str, Any]] = []
        prev_hist = None
        idx = 0
        last_saved_t = -1e9
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                t = idx / fps
                small = cv2.resize(frame, (160, 90))
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is None:
                    score = 1.0
                else:
                    score = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                prev_hist = hist
                if score >= threshold and (t - last_saved_t) * 1000 >= min_gap:
                    name = f"f_{len(frames):04d}_{t:08.2f}.jpg"
                    path = out_dir / name
                    # 注意：cv2.imwrite 在 Windows 上遇到中文路径会静默失败（返回 False）。
                    # 所以先编码成内存里的 JPEG，再用 Python 写文件。
                    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                    if not ok:
                        raise RuntimeError("关键帧 JPEG 编码失败")
                    path.write_bytes(buf.tobytes())
                    item: Dict[str, Any] = {"idx": len(frames), "time": round(t, 2), "path": str(path),
                                            "score": round(score, 4), "ocr_text": "", "caption": ""}
                    if ocr and settings.enable_ocr:
                        try:
                            item["ocr_text"] = "\n".join(x[1] for x in (self.ocr(path) or []) if len(x) > 1)
                        except Exception as exc:  # noqa: BLE001
                            item["ocr_text"] = f"(OCR 失败: {exc})"
                    frames.append(item)
                    last_saved_t = t
                    if len(frames) >= max_frames:
                        break
                if progress and duration > 0 and idx % (step * 40) == 0:
                    progress(min(99.0, t / duration * 100.0), f"扫描画面 {int(t)}s / {int(duration)}s")
            idx += 1
        cap.release()
        if progress:
            progress(100.0, f"关键帧完成，共 {len(frames)} 张")
        return frames

    def ocr(self, image_path: str | Path) -> List[Dict[str, Any]]:
        engine = _get_ocr()
        result, _ = engine(str(image_path))
        out: List[Dict[str, Any]] = []
        for row in result or []:
            try:
                box, text, score = row[0], row[1], float(row[2])
                out.append({"text": text, "score": score, "box": box})
            except Exception:  # noqa: BLE001
                continue
        return out

    def describe(self) -> Dict[str, Any]:
        ocr_ready = False
        try:
            import rapidocr_onnxruntime  # noqa: F401

            ocr_ready = True
        except ImportError:
            pass
        return {"provider": self.name, "ocr_installed": ocr_ready, "ocr_enabled": settings.enable_ocr}


class VLMVision(VisionProvider):
    """把图片 base64 发给 OpenAI 兼容的视觉模型（硅基流动 Qwen2.5-VL、GLM-4V、GPT-4o 都行）。"""

    name = "vlm"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        # 优先用 VLM 专属配置，没填就复用大模型那套（同一个 key 往往也能用）
        self.base_url = (base_url or settings.vlm_base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else (settings.vlm_api_key or settings.llm_api_key)
        self.model = model or settings.vlm_model or settings.llm_model

    def caption(self, image_path: str | Path, prompt: str = "") -> str:
        if not self.api_key:
            return ""
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content = [
            {"type": "text", "text": prompt or "用一句中文描述这一帧画面里的信息（人物、场景、屏幕上的文字）。没有有效信息就回答“无”。"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": [{"role": "user", "content": content}], "max_tokens": 200},
                )
            if resp.status_code >= 400:
                return ""
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception:  # noqa: BLE001
            return ""


@register_provider("vision", "local_opencv")
def _make_cv() -> VisionProvider:
    return OpenCVVision()


@register_provider("vision", "vlm")
def _make_vlm() -> VisionProvider:
    return VLMVision()
