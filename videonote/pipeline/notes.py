# -*- coding: utf-8 -*-
"""notes —— 摘要与笔记（map-reduce 大模型，没配 key 就退回本地抽取式）。

产出两份：
  notes.md    人能读的 Markdown 笔记（章节时间轴 / 要点 / 术语 / 金句 / 跟读素材）
  notes.json  前端结构化展示用
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..models import Transcript
from ..providers.base import get_llm_provider

CHUNK_CHARS = 4200

SYSTEM = (
    "你是一个专业的视频内容分析师，擅长把长视频转写成的高质量笔记。"
    "你会严格基于给定材料，不编造事实。所有结论都要能追溯到材料里的时间点。"
)

MAP_PROMPT = """下面是视频《{title}》转写稿的第 {i}/{n} 段（带时间戳）。

请输出 JSON（不要解释文字）：
{{
  "summary": "本段 2-4 句中文概括",
  "points": ["要点，每条一句话"],
  "terms": [{{"term": "专有名词/术语/生词/俚语", "meaning": "中文解释", "context": "出现的原句片段"}}],
  "quotes": [{{"time": "00:01:23", "text": "值得记的原话", "note": "为什么值得记"}}],
  "speaking_material": [{{"time": "00:01:23", "line": "适合口语跟读的原句", "why": "地道表达/连读/常用句型"}}]
}}

规则：
- points 3-8 条；terms 只收录真正需要解释的（0-8 条）；quotes 与 speaking_material 各 0-4 条，没有就给空数组。
- 以「（画面）」开头的是从视频画面里读来的信息（板书/PPT/代码/演示操作），要当成内容理解的一部分。
- 如果材料是英文，speaking_material 的 line 保留英文原文，why 用中文说明。
- 时间戳只用材料中出现过的。

材料：
{body}
"""

REDUCE_PROMPT = """下面是对视频《{title}》各分段的阶段分析结果（JSON 数组，含时间戳）。

请合并成一份完整的中文学习笔记，输出 JSON（不要解释文字）：
{{
  "title": "给这个视频起一个准确的中文标题",
  "one_liner": "一句话总结（40 字内）",
  "summary_markdown": "800-2000 字的详细笔记正文，Markdown 格式，用 ## 小标题分节（如 讲了什么 / 核心内容 / 关键结论 / 值得注意的细节）。要有信息密度，不要空话。",
  "chapters": [{{"start": "00:00:00", "title": "章节标题", "points": ["该章节的要点"]}}],
  "key_points": ["全片最重要的 5-10 条结论"],
  "glossary": [{{"term": "", "meaning": "", "context": ""}}],
  "quotes": [{{"time": "", "text": "", "note": ""}}],
  "action_items": ["看完之后可以做的事（学习/行动建议）"],
  "speaking_material": [{{"time": "", "line": "", "why": ""}}]
}}

要求：
- chapters 按时间顺序，覆盖全片，5-12 个；start 用 MM:SS 或 HH:MM:SS。
- glossary 合并去重，最多 20 条，优先保留对理解内容最关键或最适合学的表达。
- speaking_material 挑 8-20 条最适合跟读的句子（地道、有连读、长度 5-20 词）。
- 全部使用简体中文叙述（原文引用除外）。

分段分析：
{body}
"""


@register_stage
class NotesStage(Stage):
    name = "notes"
    label = "摘要与笔记"
    description = "map-reduce 生成摘要、章节时间轴、术语表、金句、跟读素材"
    requires = ("transcript",)
    produces = "notes"
    optional = True

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        data = ctx.load_json("transcript") or {}
        tr = Transcript.from_dict(data)
        title = ctx.project.get("title") or "未命名视频"
        if not tr.segments:
            raise RuntimeError("没有转写结果，无法生成笔记")

        provider = get_llm_provider(ctx.params.get("llm_provider"))
        captions = [(float(f.get("time", 0)), (f.get("caption") or "").strip())
                    for f in (ctx.load_json("frames") or []) if (f.get("caption") or "").strip()]
        if captions:
            ctx.log(f"把 {len(captions)} 条画面描述一起喂给摘要")
        if getattr(provider, "available", True):
            ctx.log(f"使用大模型生成笔记: {provider.describe()}")
            try:
                payload = self._llm_notes(ctx, provider, tr, title, captions)
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"大模型生成失败（{exc}），改用本地抽取式摘要", "warn")
                payload = _extractive(tr, title)
        else:
            ctx.log("未配置大模型 key，使用本地抽取式摘要（配置 VN_LLM_API_KEY 可获得高质量笔记）", "warn")
            payload = _extractive(tr, title)
        if captions:
            payload["visual_notes"] = [{"time": _ts(t), "caption": c} for t, c in captions]

        md = render_markdown(payload, title, tr)
        md_path = ctx.artifact_path("notes", "notes.md")
        md_path.write_text(md, encoding="utf-8")
        meta = {"title": payload.get("title") or title, "one_liner": payload.get("one_liner", ""),
                "engine": payload.get("_engine", "llm")}
        from .. import db

        db.put_artifact(ctx.project_id, "notes", str(md_path), kind="markdown", meta=meta)
        ctx.save_json("summary", payload, "summary.json", meta=meta)
        ctx.set_progress(100, "笔记完成")
        return {"notes": str(md_path), "engine": meta["engine"]}

    # ------------------------------------------------------------------
    def _llm_notes(self, ctx: StageContext, provider, tr: Transcript, title: str,
                   captions: Optional[List[tuple]] = None) -> Dict[str, Any]:
        chunks = _chunk(tr, CHUNK_CHARS, captions)
        ctx.log(f"转写稿切成 {len(chunks)} 段做 map")
        partials: List[Dict[str, Any]] = []
        for i, body in enumerate(chunks):
            ctx.check_cancel()
            ctx.set_progress(i / max(1, len(chunks)) * 70.0, f"分析第 {i+1}/{len(chunks)} 段")
            try:
                out = provider.complete_json(
                    MAP_PROMPT.format(title=title, i=i + 1, n=len(chunks), body=body),
                    system=SYSTEM, retries=1,
                )
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"第 {i+1} 段分析失败: {exc}", "warn")
                continue
            if isinstance(out, dict):
                out["_range"] = body.splitlines()[0][:12] if body else ""
                partials.append(out)
        if not partials:
            raise RuntimeError("所有分段分析都失败")

        ctx.set_progress(80, "汇总全片笔记")
        reduce_body = _json_dumps(partials)
        payload = provider.complete_json(
            REDUCE_PROMPT.format(title=title, body=reduce_body[:60000]),
            system=SYSTEM, retries=2,
        )
        if not isinstance(payload, dict):
            raise ValueError("汇总结果不是 JSON 对象")
        payload["_engine"] = "llm"
        payload.setdefault("title", title)
        return payload


# --------------------------------------------------------------------------
# 本地抽取式兜底（不需要任何 API key）
# --------------------------------------------------------------------------
STOP = set("""the a an and or but if then than that this these those of in on at to for with from by as is are was were be been being it its i you he she they we me my your his her their our not no do does did have has had will would can could should may might must about into over after before out up down so very just also more most some such only own same too s t don now here there what which who whom when where why how all any both each few other""".split())


def _extractive(tr: Transcript, title: str) -> Dict[str, Any]:
    segs = tr.segments
    total = len(segs)
    words = re.findall(r"[A-Za-z']{3,}|[\u4e00-\u9fa5]{2,}", " ".join(s.text for s in segs).lower())
    freq = Counter(w for w in words if w not in STOP)
    keywords = [w for w, _ in freq.most_common(20)]

    def score(s) -> float:
        toks = re.findall(r"[A-Za-z']{3,}|[\u4e00-\u9fa5]{2,}", s.text.lower())
        if not toks:
            return 0.0
        return sum(freq.get(t, 0) for t in toks) / (len(toks) ** 0.6)

    ranked = sorted(range(total), key=lambda i: score(segs[i]), reverse=True)[:12]
    ranked.sort()
    key_points = [{"idx": i} for i in ranked]

    n_ch = max(3, min(10, total // 25 or 3))
    step = max(1, total // n_ch)
    chapters = []
    for i in range(0, total, step):
        block = segs[i : i + step]
        if not block:
            continue
        bs = sorted(block, key=score, reverse=True)[:2]
        chapters.append({
            "start": _ts(block[0].start),
            "title": (bs[0].text[:36] if bs else f"片段 {i//step+1}").strip(),
            "points": [f"{_ts(s.start)} {s.text[:80]}" for s in sorted(bs, key=lambda x: x.start)],
        })

    quotes = [{"time": _ts(segs[i].start), "text": segs[i].text, "note": "高频关键词集中的句子"}
              for i in ranked[:5]]
    speaking = []
    for s in segs:
        wc = len(s.text.split())
        if 5 <= wc <= 20 and s.text.strip().endswith((".", "?", "!")):
            speaking.append({"time": _ts(s.start), "line": s.text.strip(), "why": "长度适合跟读"})
        if len(speaking) >= 12:
            break

    return {
        "_engine": "extractive",
        "title": title,
        "one_liner": f"共 {total} 句，约 {tr.duration/60:.1f} 分钟；关键词：{'、'.join(keywords[:6])}",
        "summary_markdown": "## 说明\n本笔记由**本地抽取式摘要**生成（未配置大模型 API key）。"
                            "它只做关键词与句子排序，不做真正的理解。\n\n"
                            "## 高频词\n" + "、".join(keywords) + "\n\n"
                            "## 内容主线（按时间）\n"
                            + "\n".join(f"- **{_ts(segs[i].start)}** {segs[i].text[:100]}" for i in ranked),
        "chapters": chapters,
        "key_points": [f"{_ts(segs[i].start)} {segs[i].text[:90]}" for i in ranked],
        "glossary": [{"term": w, "meaning": "（本地模式无法解释，配置大模型后可自动生成释义）", "context": ""}
                     for w in keywords[:10]],
        "quotes": quotes,
        "action_items": [],
        "speaking_material": speaking,
    }


# --------------------------------------------------------------------------
def _chunk(tr: Transcript, size: int, captions: Optional[List[tuple]] = None) -> List[str]:
    """把转写（和画面描述）按时间顺序拼成若干段材料。"""
    items: List[tuple] = [(float(s.start), f"[{_ts(s.start)}] {s.text.strip()}") for s in tr.segments]
    for t, cap in captions or []:
        items.append((float(t), f"[{_ts(t)}] （画面）{cap}"))
    items.sort(key=lambda x: x[0])

    chunks, buf, cur = [], [], 0
    for _, line in items:
        if cur + len(line) > size and buf:
            chunks.append("\n".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += len(line) + 1
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _ts(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{int(s):02d}" if h else f"{m:02d}:{int(s):02d}"


def render_markdown(p: Dict[str, Any], title: str, tr: Transcript) -> str:
    L: List[str] = []
    L.append(f"# {p.get('title') or title}")
    if p.get("one_liner"):
        L.append(f"> {p['one_liner']}")
    L.append("")
    L.append(f"*时长 {tr.duration/60:.1f} 分钟 · {len(tr.segments)} 句 · 语言 {tr.language or '未知'} · "
             f"识别 {tr.provider or '-'} ({tr.model or '-'}) · 摘要引擎 {p.get('_engine','llm')}*")
    if p.get("summary_markdown"):
        L += ["", "## 内容摘要", "", str(p["summary_markdown"]).strip()]
    if p.get("key_points"):
        L += ["", "## 关键要点", ""] + [f"- {x}" for x in p["key_points"]]
    if p.get("chapters"):
        L += ["", "## 章节时间轴", ""]
        for c in p["chapters"]:
            L.append(f"### [{c.get('start','')}] {c.get('title','')}")
            for pt in c.get("points") or []:
                L.append(f"- {pt}")
            L.append("")
    if p.get("glossary"):
        L += ["", "## 术语 / 生词表", "", "| 词条 | 解释 | 出现语境 |", "| --- | --- | --- |"]
        for g in p["glossary"]:
            term = str(g.get("term", "")).replace("|", "\\|")
            mean = str(g.get("meaning", "")).replace("|", "\\|")
            ctxt = str(g.get("context", "")).replace("|", "\\|")
            L.append(f"| {term} | {mean} | {ctxt} |")
    if p.get("quotes"):
        L += ["", "## 金句", ""]
        for q in p["quotes"]:
            L.append(f"- **[{q.get('time','')}]** {q.get('text','')}  \n  <sub>{q.get('note','')}</sub>")
    if p.get("speaking_material"):
        L += ["", "## 口语跟读素材", ""]
        for s in p["speaking_material"]:
            L.append(f"- `[{s.get('time','')}]` {s.get('line','')}  \n  <sub>{s.get('why','')}</sub>")
    if p.get("action_items"):
        L += ["", "## 行动建议", ""] + [f"- [ ] {x}" for x in p["action_items"]]
    if p.get("visual_notes"):
        L += ["", "## 画面信息（视觉模型读到的）", ""]
        for v in p["visual_notes"]:
            L.append(f"- **[{v.get('time','')}]** {v.get('caption','')}")
    L.append("")
    return "\n".join(L)
