"""Provider 包。base.py 定义抽象，其余模块用 @register_provider 注册自己。"""
from .base import (  # noqa: F401
    ASRProvider,
    Diarizer,
    LLMProvider,
    VisionProvider,
    get_asr_provider,
    get_diarizer,
    get_llm_provider,
    get_vision_provider,
)
