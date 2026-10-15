"""领域模型：贯穿 provider / pipeline / API 的数据结构。"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------
# 媒体
# --------------------------------------------------------------------------
@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = True
    title: str = ""
    source_type: str = "file"  # file | url
    source_uri: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 字幕 / 转写
# --------------------------------------------------------------------------
@dataclass
class Segment:
    idx: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    words: list[dict] = field(default_factory=list)  # [{w,start,end,prob}]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Transcript:
    language: str = ""
    duration: float = 0.0
    model: str = ""
    provider: str = ""
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    def to_dict(self) -> dict:
        return {
            "language": self.language,
            "duration": self.duration,
            "model": self.model,
            "provider": self.provider,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        segs = [Segment(**{k: v for k, v in s.items() if k in Segment.__annotations__}) for s in data.get("segments", [])]
        return cls(
            language=data.get("language", ""),
            duration=float(data.get("duration", 0) or 0),
            model=data.get("model", ""),
            provider=data.get("provider", ""),
            segments=segs,
        )


# --------------------------------------------------------------------------
# 画面
# --------------------------------------------------------------------------
@dataclass
class Frame:
    idx: int
    time: float
    path: str
    score: float = 0.0          # 场景变化强度
    ocr_text: str = ""          # 画面硬字幕/文字
    caption: str = ""           # 视觉大模型/启发式描述

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------
class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(raw: Optional[str], default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
