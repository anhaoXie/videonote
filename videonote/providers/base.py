"""Provider 抽象层 —— 每个能力都留了插槽，换实现不用改业务代码。

    asr     : local(faster-whisper) / cloud(OpenAI 兼容) / 你自己的
    llm     : openai_compat(DeepSeek 等) / none
    vision  : local_opencv(+OCR) / vlm(OpenAI 兼容视觉模型) / 你自己的
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import settings
from ..models import Transcript
from ..core.registry import registry


class ASRProvider(ABC):
    name = "asr"

    @abstractmethod
    def transcribe(self, audio_path: str | Path, language: Optional[str] = None,
                   progress: Optional[Callable[[float, str], None]] = None) -> Transcript:
        ...

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name}


class LLMProvider(ABC):
    name = "llm"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        ...

    # 便利封装
    def complete(self, prompt: str, system: Optional[str] = None,
                 temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        msgs: List[Dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self.chat(msgs, temperature=temperature, max_tokens=max_tokens)


class VisionProvider(ABC):
    name = "vision"

    def ocr(self, image_path: str | Path) -> List[Dict[str, Any]]:
        return []

    def caption(self, image_path: str | Path, prompt: str = "") -> str:
        return ""

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name}


class Diarizer(ABC):
    """说话人分离：给每句话标上是谁说的。"""

    name = "diarize"

    @abstractmethod
    def diarize(self, audio_path: str, segments: List[dict],
                 num_speakers: Optional[int] = None,
                 progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
        ...

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name}


# --------------------------------------------------------------------------
# 解析器
# --------------------------------------------------------------------------
def get_asr_provider(name: Optional[str] = None) -> ASRProvider:
    key = (name or settings.asr_effective_provider()).lower()
    if not registry.has_provider("asr", key):
        raise KeyError(f"未注册的 ASR provider: {key}（可选：{sorted(registry.providers.get('asr', {}))}）")
    return registry.get_provider("asr", key)


def get_llm_provider(name: Optional[str] = None) -> LLMProvider:
    key = (name or settings.llm_provider).lower()
    if not registry.has_provider("llm", key):
        raise KeyError(f"未注册的 LLM provider: {key}（可选：{sorted(registry.providers.get('llm', {}))}）")
    return registry.get_provider("llm", key)


def get_vision_provider(name: Optional[str] = None) -> VisionProvider:
    key = (name or ("local_opencv")).lower()
    if not registry.has_provider("vision", key):
        return _NullVision()
    return registry.get_provider("vision", key)


def get_diarizer(name: Optional[str] = None) -> Diarizer:
    key = (name or settings.diarize_provider).lower()
    if not registry.has_provider("diarize", key):
        raise KeyError(f"未注册的说话人分离 provider: {key}"
                       f"（可选：{sorted(registry.providers.get('diarize', {}))}）")
    return registry.get_provider("diarize", key)


class _NullVision(VisionProvider):
    name = "none"
