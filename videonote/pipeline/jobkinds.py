# -*- coding: utf-8 -*-
"""任务类型（Job Kind）：把若干 Stage 串成一条流水线。

前端「新建任务」下拉框就是读这里的注册表，所以新增功能只要在这里加一行。
"""
from __future__ import annotations

from ..core.registry import register_job_kind

# 全流程：导入 -> 抽音频 -> 语音识别 -> 画面关键帧 -> 摘要笔记 -> 字幕
register_job_kind(
    "analyze",
    ["ingest", "audio", "asr", "frames", "notes", "subtitles"],
    description="完整分析：语音识别 + 画面分析 + 摘要笔记 + 字幕",
    label="完整分析（推荐）",
)

# 只要转写和字幕
register_job_kind(
    "transcribe",
    ["ingest", "audio", "asr", "subtitles"],
    description="只做语音识别并生成字幕，最快",
    label="转写 + 字幕",
)

# 只要笔记（对已有转写的项目重新总结）
register_job_kind(
    "summarize",
    ["notes"],
    description="重新生成摘要笔记（换模型/换提示词后用）",
    label="重做摘要笔记",
)

# 只要画面分析
register_job_kind(
    "frames",
    ["ingest", "frames"],
    description="只抽关键帧做画面分析",
    label="画面关键帧",
)

# 只导入
register_job_kind(
    "ingest",
    ["ingest"],
    description="只把媒体导入项目（下载/复制 + 读元信息）",
    label="仅导入媒体",
)

# 字幕全流程：转写 -> 翻译 -> 双语字幕 -> 压制
register_job_kind(
    "subtitle_full",
    ["ingest", "audio", "asr", "translate", "subtitles"],
    description="转写 + 翻译 + 双语字幕文件",
    label="字幕（含翻译，双语）",
)

# 硬字幕压制（慢）
register_job_kind(
    "burn",
    ["burn"],
    description="把已有字幕烧进画面，导出 mp4",
    label="压制硬字幕视频",
)

# 只翻译
register_job_kind(
    "translate",
    ["translate", "subtitles"],
    description="对已有转写做翻译，输出双语字幕",
    label="翻译成双语字幕",
)

# 只重新生成字幕文件：改完转写文本 / 译文之后用它刷新 SRT/VTT，
# 不重跑 ASR（否则一小时视频要再等一两小时）。
register_job_kind(
    "subtitles",
    ["subtitles"],
    description="用已有转写重新生成 SRT/VTT（不重跑语音识别）",
    label="重新生成字幕",
)

# 说话人分离（美剧/访谈：分清谁在说话）
register_job_kind(
    "diarize",
    ["diarize"],
    description="本地声纹模型分辨每句话是谁说的，标注说话人",
    label="说话人分离",
)

# 按说话人拆句（v0.4）：把被 ASR 合成一句的多人对话切开
# 注意：会重排句号，所以句库/评分/复习队列会跟着迁移（数据库先自动备份）
register_job_kind(
    "split_speakers",
    ["split_speakers"],
    description="把「A 说一句 + B 回一句」被合成一句的情况按说话人切开（句库/评分/复习自动改号迁移）",
    label="按说话人拆句（需先做说话人分离）",
)

# 画面描述（需要视觉大模型 key）
register_job_kind(
    "captions",
    ["captions", "notes"],          # 描述完必须重算笔记，否则画面信息进不了笔记
    description="用视觉大模型描述关键帧画面（板书/PPT/演示），并写进笔记",
    label="画面描述（视觉大模型）",
)

# 完整分析 + 说话人分离
register_job_kind(
    "analyze_plus",
    ["ingest", "audio", "asr", "frames", "captions", "diarize", "notes", "subtitles"],
    description="完整分析，并且加上画面描述与说话人分离（最全，也最慢）",
    label="完整分析（含说话人+画面描述）",
)
