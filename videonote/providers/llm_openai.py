"""大模型：OpenAI 兼容的 /chat/completions。

DeepSeek / 硅基流动 / OpenAI / 通义 / 本地 Ollama(v1) / LM Studio 全都是这个协议，
所以换供应商只要改 .env 里的 base_url + model。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings
from .base import LLMProvider
from ..core.registry import register_provider


class OpenAICompatLLM(LLMProvider):
    name = "openai_compat"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: List[Dict[str, str]], temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        if not self.api_key:
            raise RuntimeError("未配置大模型 API key（.env 里的 VN_LLM_API_KEY）")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0)) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json=payload,
                    )
                if resp.status_code >= 400:
                    raise RuntimeError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt == 2:
                    break
                import time

                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"调用大模型失败: {last_err}")

    # ---------------- 便利方法 ----------------
    def complete_json(self, prompt: str, system: Optional[str] = None, retries: int = 2) -> Any:
        """要求模型输出 JSON 并解析（容错 markdown 代码块 / 前后杂字）。"""
        text = ""
        for _ in range(retries + 1):
            text = self.complete(prompt, system=system, temperature=0.2)
            parsed = _extract_json(text)
            if parsed is not None:
                return parsed
            prompt = prompt + "\n\n注意：只输出合法 JSON，不要任何解释文字。"
        raise ValueError(f"模型没有返回可解析的 JSON：{text[:300]}")

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name, "base_url": self.base_url, "model": self.model, "key_set": bool(self.api_key)}


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    return None


class NullLLM(LLMProvider):
    """没配 key 时的兜底：抛错，让上层走「本地抽取式摘要」。"""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    def chat(self, messages: List[Dict[str, str]], temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        raise RuntimeError("未配置大模型（VN_LLM_API_KEY 为空）")


@register_provider("llm", "openai_compat")
def _make_openai_compat() -> LLMProvider:
    return OpenAICompatLLM()


@register_provider("llm", "deepseek")
def _make_deepseek() -> LLMProvider:
    return OpenAICompatLLM(base_url="https://api.deepseek.com/v1", model="deepseek-chat")


@register_provider("llm", "none")
def _make_none() -> LLMProvider:
    return NullLLM()
