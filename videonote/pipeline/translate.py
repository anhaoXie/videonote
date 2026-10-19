# -*- coding: utf-8 -*-
"""translate —— 逐句翻译，产出双语字幕（美剧/外语视频学习的关键一环）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..core.zh import to_simplified
from ..providers.base import get_llm_provider

# 简繁要求写在 system 里（权重比 user 消息高），但**不依赖它**：
# 下游还有 core.zh.to_simplified() 做确定性兜底。模型不听话时我们照样能出简体。
SYSTEM = (
    "你是专业影视字幕翻译。翻译要口语化、自然，符合角色说话的语气，不要逐字硬译。\n"
    "输出必须只使用**简体中文**：不得出现繁体字、异体字或港台用词"
    "（「這」要写「这」，「個」要写「个」，「說話」要写「说话」）。"
)

PROMPT = """把下面编号的台词翻译成{target}。

要求：
- 保留编号，输出 JSON 对象：{{"0": "译文", "1": "译文"}}，不要任何解释。
- 每句译文简洁，适合做字幕（尽量不超过 40 字）。
- 人名、专有名词保持通用译法。
- 一律用简体中文，不要出现繁体字。

台词：
{body}
"""

BATCH = 40


@register_stage
class TranslateStage(Stage):
    name = "translate"
    label = "翻译字幕"
    description = "把转写逐句翻译成中文，生成双语字幕"
    requires = ("transcript",)
    produces = "translation"
    optional = True

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        segs = ctx.segments()
        if not segs:
            raise RuntimeError("没有句子可翻译")
        provider = get_llm_provider(ctx.params.get("llm_provider"))
        if not getattr(provider, "available", True):
            raise RuntimeError("翻译需要大模型 API key（VN_LLM_API_KEY）")
        target = ctx.params.get("target_language", "简体中文")

        out: Dict[str, str] = {}
        total = len(segs)
        fixed = 0
        for i in range(0, total, BATCH):
            ctx.check_cancel()
            block = segs[i : i + BATCH]
            body = "\n".join(f'{s["idx"]}. {s["text"]}' for s in block)
            ctx.set_progress(i / total * 100.0, f"翻译 {i}/{total} 句")
            try:
                data = provider.complete_json(PROMPT.format(target=target, body=body), system=SYSTEM, retries=2)
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"第 {i//BATCH+1} 批翻译失败: {exc}", "warn")
                continue
            if isinstance(data, dict):
                for k, v in data.items():
                    key = str(k).strip()
                    if str(key).isdigit():
                        raw = str(v).strip()
                        simp = to_simplified(raw)
                        if simp != raw:
                            fixed += 1
                        out[str(key)] = simp
        if not out:
            raise RuntimeError("翻译结果为空")
        if fixed:
            ctx.log(f"{fixed} 句译文含繁体字，已自动转成简体（模型没有完全遵守 prompt）", "warn")

        ctx.save_json("translation", out, "translation.json", meta={"count": len(out), "target": target})
        ctx.set_progress(100, f"翻译完成 {len(out)}/{total} 句")
        return {"translated": len(out), "total": total}
