# -*- coding: utf-8 -*-
"""asr —— 语音识别成带时间戳的句子，同时写入 segments 表（供播放器/跟读/字幕查询）。"""
from __future__ import annotations

from typing import Any, Dict

from .. import db
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..core.zh import to_simplified
from ..providers.base import get_asr_provider


@register_stage
class ASRStage(Stage):
    name = "asr"
    label = "语音识别"
    description = "faster-whisper(本地) 或 OpenAI 兼容云端接口，输出逐句时间轴"
    requires = ("audio",)
    produces = "transcript"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        audio = ctx.artifact_file("audio")
        if not audio:
            raise RuntimeError("找不到音频文件，请先跑 audio 阶段")
        provider_name = ctx.params.get("asr_provider")
        provider = get_asr_provider(provider_name)
        ctx.log(f"使用 ASR: {provider.describe()}")
        language = ctx.params.get("language") or None

        tr = provider.transcribe(audio, language=language, progress=ctx.set_progress)
        if not tr.segments:
            raise RuntimeError("识别结果为空（视频可能没有人声）")

        # 后处理：合并过短/重复的碎句，句子更适合跟读
        tr.segments = _tidy(tr.segments)

        # 繁简归一。这是**真正**的源头：Whisper 转中文时经常直接输出繁体
        # （「上網站」「老師聽說」），字幕文本就来自这里，跟翻译阶段无关。
        # 放在 ASR 出口做一次，下游的转写视图 / 字幕 / 笔记 / Anki 导出全都干净。
        fixed = _normalize_zh(tr.segments)
        if fixed:
            ctx.log(f"{fixed} 句识别结果含繁体字，已统一为简体", "warn")

        ctx.save_json("transcript", tr.to_dict(), "transcript.json",
                      meta={"language": tr.language, "segments": len(tr.segments),
                            "provider": tr.provider, "model": tr.model})
        db.replace_segments(ctx.project_id, [s.to_dict() for s in tr.segments])
        # 重新识别会重新编号句子，之前的拆句结果随之作废。
        # 不删的话 split_speakers 会因为「已有产出」被静默跳过，留下没拆的句子。
        db.delete_artifact(ctx.project_id, "split_report")
        db.update_project(ctx.project_id, duration=tr.duration or ctx.project.get("duration") or 0)

        srt = _fmt_srt(tr)
        p = ctx.artifact_path("transcript", "transcript.txt")
        p.write_text(srt, encoding="utf-8")
        db.put_artifact(ctx.project_id, "transcript_text", str(p), kind="text")

        ctx.set_progress(100, f"识别完成：{len(tr.segments)} 句 / 语言 {tr.language or '未知'}")
        return {"segments": len(tr.segments), "language": tr.language, "provider": tr.provider,
                "model": tr.model, "duration": tr.duration}


def _normalize_zh(segments: list) -> int:
    """把识别结果里的繁体统一成简体，返回被改动的句数。

    连词级时间戳（words）一起转 —— 否则「按说话人拆句」是拿 words 拼句子，
    会把繁体又拼回来。英文内容经过转换是原样返回，不会被动到。
    """
    n = 0
    for s in segments:
        raw = s.text or ""
        simp = to_simplified(raw)
        if simp != raw:
            s.text = simp
            n += 1
        for w in (getattr(s, "words", None) or []):
            if isinstance(w, dict) and isinstance(w.get("word"), str):
                w["word"] = to_simplified(w["word"])
    return n


def _tidy(segments: list, max_gap: float = 0.9, max_dur: float = 12.0, min_dur: float = 0.35) -> list:
    """把 Whisper 的碎句拼成人话长度的句子，并重新编号。"""
    out: list = []
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        if out:
            prev = out[-1]
            gap = s.start - prev.end
            same_para = gap <= max_gap and (prev.end - prev.start) + (s.end - s.start) <= max_dur
            prev_text = prev.text.strip()
            ends_sentence = prev_text.endswith((".", "?", "!", "。", "？", "！", "…", '"'))
            if same_para and not ends_sentence:
                prev.text = (prev_text + " " + text).strip()
                prev.end = s.end
                if s.words:
                    prev.words.extend(s.words)
                continue
        s.text = text
        out.append(s)
    for i, s in enumerate(out):
        s.idx = i
    return out


def _fmt_srt(tr) -> str:
    lines = []
    for s in tr.segments:
        lines.append(f"[{_ts(s.start)} - {_ts(s.end)}] {s.text}")
    return "\n".join(lines)


def _ts(seconds: float) -> str:
    m, sec = divmod(max(0.0, float(seconds)), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{sec:06.3f}"
