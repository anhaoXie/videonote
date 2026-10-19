# 更新日志

本文件记录每个版本的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)，
版本号遵循语义化版本（MAJOR.MINOR.PATCH）。

## [0.4.1] - 2026-10-19

### 修复
- **界面半死状态**：`renderTabs()` 在模块求值阶段就重写 `#content`，把空状态里的
  `#btn-new2` 提前删除，导致 `wireChrome()` 抛 `TypeError` 并中断**其后全部接线**——
  「开始」「取消」「关闭」、三个渠道按钮都不再生效，且能力清单从未加载（两个下拉框永远是空的）。
  新增 `wire()` 容错助手 + `boot()` try/catch + 事件委托 `[data-open-import]`。
- **进度数值反复横跳**：`begin_stage()` 未重置 `_last_pct`，新阶段的起步进度被节流整段吞掉；
  前端 `setProgress()` 允许数值变小且不区分 job。改为按 job 隔离、只增不减，
  并在任务排队时就亮出进度条。
- **字幕输出繁体字**：**根因是 ASR 而不是翻译**——Whisper 转中文音频时常直接输出繁体
  （這/網站/老師/聽說），而字幕文本取自转写结果，根本不经过翻译阶段。
  修在 ASR 出口（连词级时间戳一起归一，否则「按说话人拆句」会把繁体拼回来）+ 字幕合成时
  主板文字与译文各转一次；新增 `tools/fix_traditional.py` 就地修复已有项目。

### 变更
- **导入弹窗重做**为三步：先选渠道（一次只出现当前渠道的输入）→ 再选文件（显示文件名/大小）
  → 选项（流水线选择收进「⚙ 导入后跑什么」折叠区，默认完整分析）。
  「开始」固定在底部；大文件改为非阻塞提示 + 一键切「本机路径」。
- 弹窗头部/底部固定、仅中间滚动，避免按钮被长内容推出视野。

### 新增
- `videonote/core/zh.py`（繁简规范化）
- `tools/test_zh_subtitle.py`（用故意返回繁体的假模型验证落库为简体，14 项）
- `tools/fix_traditional.py`（就地修复已有项目的繁体，默认 dry-run + 自动备份）
- 任务类型 `subtitles`（「重新生成字幕」：只跑字幕，不重跑 ASR）
- `docs/上线清单.md`（上线就绪度评估）
- `docs/screenshots/`（8 张界面截图，只用仓库自带 samples）

### 其他
- `?v=` 缓存破除参数 0.3.0 → 0.4.3（此前从未改过，老用户会一直吃浏览器缓存）
- 依赖新增 `opencc-python-reimplemented`

## [0.4.0] - 2026-10-19

### 新增
- **统一复习队列**：句子卡 + 单词卡按比例混排（`srs.unified_queue` / `unified_counts`），
  单词卡走同一套 SM-2 调度；新增 `word_reviews` 表与 `/api/reviews/words*` 端点。
- **按说话人拆句**（`split_speakers` 阶段）：ASR 常把「A 说一句 + B 回一句」并成一句，
  用词级时间戳按说话人边界切开。会**重排句子编号**，`clips` / `scores` / `reviews` /
  `practice_log` 按主键 `id` 逐行迁移，迁移前自动用 SQLite backup API 备份数据库。
  句层切分保证单射，不会撞 `clips` 的主键约束。
- 概览页新增「按说话人拆句」按钮；`job_kinds` 注册 `split_speakers`。

### 修复
- `diarize_local` 的 pyannote 分支补产出 `windows`（拆句依赖它）。
- `asr` / `diarize` 重跑时删除 `split_report`，否则拆句会被静默跳过。
- `tools/test_diarize.py` 跑完自清理（此前每跑一次就多留一个项目）。

### 其他
- 本项目此前**没有版本控制**，本次建立 git 仓库并补 `.gitignore`。

## [0.3.0]

### 新增
- 音素级发音评测（Wav2Vec2 CTC + espeak 音素对齐，GOP 打分）
- 最小对立对单词训练（35 对，TTS 示范音 + 听音辨词 + 读词评分）
- SM-2 复习队列与训练统计

## [0.2.0]

### 新增
- 跟读句库 + Anki（.apkg）导出
- Whisper 回环发音评分
- ECAPA 说话人分离、影子跟读
- VLM 画面描述

## [0.1.0]

### 新增
- 导入（本地文件 / 本机路径 / 视频链接）、ASR、关键帧、map-reduce 摘要
- SRT / VTT 字幕、双语翻译、硬字幕压制、逐句跟读
- SSE 进度推送、插件注册中心（Stage / Provider / Router / JobKind 自动发现）
