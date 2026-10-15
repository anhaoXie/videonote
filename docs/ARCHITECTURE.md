# 架构说明

## 一句话

**HTTP 接口 → 任务队列 → 管线（一串 Stage）→ 产出物（artifact）→ 前端展示**。
所有能力都是插件，注册到 `videonote/core/registry.py` 的注册中心里。

---

## 1. 数据流

```
                      ┌──────────────── 任务队列（SQLite jobs 表 + 单线程 Worker）────────────────┐
导入视频/链接 ──POST──▶│ analyze / transcribe / summarize / diarize / captions / subtitle_full …│
                      └───────────────────────────────┬────────────────────────────────────────┘
                                                      ▼
  ingest ──▶ [media_file, media_info]  媒体归位（复制或 yt-dlp 下载）+ PyAV 探测时长/分辨率
     │
     ├─▶ audio ──▶ [audio]            抽 16k 单声道 wav（本地/云端 ASR/声纹 共用）
     │      ├─▶ asr ──▶ [transcript]        faster-whisper 或云端 → 逐句时间轴 + words
     │      │                          └─▶ segments 表（播放器/跟读/字幕/搜索都查它）
     │      ├─▶ diarize ──▶ [diarization]   ECAPA 声纹(滑窗) + 层次聚类 → 每句标说话人
     │      │                              同时产出 windows 窗口级时间线（说话人时间轴）
     │      ├─▶ split_speakers ──▶ [split_report]  按词级时间戳把「多人被合成一句」切开
     │      │                              ⚠ 句号重排：clips/scores/reviews/译文 自动迁移，先备份数据库
     │      ├─▶ notes ──▶ [notes, summary]  map-reduce 大模型摘要（含画面描述）；无 key 退回抽取式
     │      ├─▶ translate ──▶ [translation] 逐句翻译（双语字幕）
     │      ├─▶ subtitles ──▶ [srt/vtt]     SRT/VTT 导出
     │      └─▶ burn ──▶ [video_hardsub]    ffmpeg 压制硬字幕
     ├─▶ frames ──▶ [frames, frames_sheet]  场景检测 + 关键帧（+ 可选 OCR）
     │      └─▶ captions ──▶ [frames_captioned]  视觉大模型描述每帧 → 写回 frames + 进笔记
     └─▶（训练侧，不走任务队列，HTTP 直调）
            score   : 录音 → 解码 → Whisper 回环 → 逐词对齐 → 分数（routes_practice.py）
            words   : 合成音 + 你的录音 → 音素判定 → pair_drills（流水）+ word_reviews（调度）
            clips   : 收藏句子 → ffmpeg 切音频/视频 → clips 表 + Anki 导出（routes_clips.py）
```

**Artifact（产出物）是唯一的阶段间契约**：每个 Stage 声明 `requires` 和 `produces`，
所以任何阶段都能单独重跑、跳过、替换实现，而不用改别的阶段。

---

## 2. 目录职责

| 路径 | 作用 |
| --- | --- |
| `videonote/core/registry.py` | 插件注册中心：Stage / Provider / Router / JobKind 都注册在这里；`autodiscover()` 自动导入 `providers/` 与 `pipeline/` 下的模块 |
| `videonote/core/stages.py` | `Stage` 基类、`StageContext`（进度/日志/artifact 读写）、`PipelineRunner` |
| `videonote/core/jobs.py` | 单线程 Worker：领任务 → 跑管线 → 写状态 → 发事件；启动时清理上次中断的任务 |
| `videonote/core/media.py` | ffmpeg（imageio-ffmpeg 自带）/ PyAV：探测、抽音、压制、切片 |
| `videonote/core/events.py` | 进程内事件总线 → SSE 推给浏览器（进度条、日志、任务状态） |
| `videonote/providers/` | 可替换实现：`asr`（local / cloud）、`llm`（openai_compat / none）、`vision`（local_opencv / vlm） |
| `videonote/pipeline/` | 具体处理阶段；`jobkinds.py` 把阶段串成任务类型 |
| `videonote/api/` | FastAPI 路由；插件注册的 router 也会挂到 `/api` 下 |
| `web/` | 原生 ES Module 前端，无构建步骤；`js/features/*.js` 一个文件一个功能页 |
| `data/` | 运行时数据：`videonote.db`、`projects/<id>/`（media/audio/frames/artifacts）、`models/`（Whisper 模型） |

---

## 3. 数据库

SQLite（`data/videonote.db`，WAL 模式），11 张表：

| 表 | 说明 |
| --- | --- |
| `projects` | 一个视频 = 一个项目：标题、来源、`media_path`、时长、状态 |
| `artifacts` | 每个阶段的产出：`(project_id, key)` 唯一，`path` + `meta` JSON |
| `segments` | 逐句时间轴：`idx/start/end/text/speaker/words` —— 播放器、跟读、字幕、搜索、说话人筛选都查它 |
| `jobs` | 任务：`kind/status/progress/message/error/params/result` |
| `job_logs` | 任务日志（每行带时间戳，前端任务详情页展示） |
| `clips` | 跟读句库：收藏的句子 + 切好的音视频路径 + 标签/备注/复习次数（`(project_id, seg_idx)` 唯一） |
| `scores` | 发音评分记录：总分 + 准确/完整/流利 + 逐词明细 JSON + 用户录音路径；`mode` 区分 `repeat`/`shadow`。明细里还存了 `phoneme_words` / `phoneme_errors`（音素级结果） |
| `reviews` | 复习队列（SM-2）：`ease / interval_days / reps / lapses / due_at / status`，`(project_id, seg_idx)` 唯一 |
| `practice_log` | 训练流水：每次评分一行（`kind / score / seconds / day`），统计页的数据源 |
| `pair_drills` | 单词训练流水：`pair_id / word / kind(listen|say) / correct / score / detail` |
| `word_reviews` | 单词卡的复习调度（SM-2）：`(pair_id, word)` 唯一。**粒度是"词对+具体单词"而不是词对** —— 同一个 iː/ɪ 里你可能读得对 `sheep`、读不对 `ship` |

加字段很随意：`ALTER TABLE` 或在 `db.py` 的 `SCHEMA` 里加一行（首次创建时生效）。

**多条写操作需要原子性时用 `db.transaction()`**（不要直接调 `db.execute()` —— 它每次都 commit，
会把事务提前截断）。唯一非它不可的场景是**段落改号**：那种操作要同时重写 `segments` 并重映射
`clips`/`scores`/`reviews`/`practice_log`，中途失败会留下「句子已换号、句库还指着旧号」的
静默错位状态，比干脆不做更糟。

---

## 3.5 学习引擎（发音训练是怎么长出来的）

```
                    录一句 ──► /projects/{pid}/score
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                            ▼
        ① 词级（core/scoring.py）        ② 音素级（core/phonemes.py）
        Whisper 回环识别 → 逐词对齐       只对「读错/含糊」的词切片
        准确/完整/流利 + 逐词标色         espeak→IPA + wav2vec2 + CTC 强制对齐 + GOP
                    │                            │
                    └─────────────┬──────────────┘
                                  ▼
                       ③ 复习调度（core/srs.py）
              句子：分数 <80 或收藏过 → 自动进 reviews
              单词：关键音读错必进 word_reviews（粒度 = 词对 + 具体单词）
                                  │
                                  ▼
                       ④ 统一今日队列（srs.unified_queue）—— 两张表按比率交错
                                   │
                                   ▼
                       ⑤ 统计（core/analytics.py）
         趋势 / 打卡 / 高频漏词 / 高频错音 / 最该重练的句子 / 复习负载 / 单词进度
```

设计要点：

* **分层评分**：词级回答「别人能不能听懂」，音素级回答「嘴该怎么改」。
  两层都能独立失败：音素模型没装、没下载、没录上声音时，词级评分照常工作。
* **只对可疑词做音素推理**：整句都跑音素模型在 CPU 上明显更慢而收益很小。
  可疑词 = 识别听成别的词，或听对了但词置信度 < 0.6。
* **宁可放过、不可错杀**：静音护栏（RMS 过低直接说"没听清"）、
  记法等价（`Xɹ ↔ X`、长音符号、`ə ↔ ɚ`）、
  `weak`（含糊）与 `sub/del`（真错）分开计数 —— 误报"你读错了"比漏报更打击人。
* **复习队列是唯一的"今天要做什么"**：收藏和读错都会进队列，
  完成一次评分就自动推进调度，不需要用户手动管理学习计划。
* **单词层用合成音，句子层用真实音**：最小对立对（ship/sheep）的词在用户视频里不一定出现过，
  所以参考音用 espeak 现场合成（`core/tts.py`，离线零成本）。但**判定永远依据 IPA 目标 + 用户自己的录音**，
  因为音素模型在"机器人念孤立单词"上属于域外数据（实测 35 对里只有 13 对能被模型确认分辨）。
  这 13 对用于「听音辨词」（分辨不出就不该出题），全部 35 对用于「跟读对比」。
* **统一队列是"视图"不是"表"**：句子（`reviews`，键 `(project_id, seg_idx)`）和单词
  （`word_reviews`，键 `(pair_id, word)`）根本没有共同的键。硬塞进一张表要么放宽唯一约束
  弄脏句子语义，要么加一列可空字段、处处判空。所以两张表各管一种卡，
  `srs.unified_queue()` 在**读取时**按比率交错合并，每一项带 `card_type` 供前端分流。
* **关键音优先于整词准确率**：`think` 读成 `sink` 时整词准确率仍有 50%，用整词分会让读错的人
  "过关"。所以关键音错了就把分数封顶到 40（`srs.KEY_PHONE_MISS_CAP`），必然落回「还在学」。
* **句子序号是隐式外键**：`clips` / `scores` / `reviews` / `practice_log` 都按
  `(project_id, seg_idx)` 存数据。任何**重排句子**的操作都必须同时迁移它们 ——
  见 `core/split.py` 的 `migrate()`（在 `db.transaction()` 里一次改完，并先把库备份到
  `data/backups/`）。落位规则刻意分成两种，因为它们回答的问题不同：
  **句库按自己的时间区间**（clip 自带 start/end，重叠最长的那段就是它；切片文件因此仍然有效），
  **评分/复习按最长子句**（那是原句的主体，用户在上面练的时间最久）。

---

## 4. HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 存活 + worker 忙闲 |
| GET | `/api/capabilities` | 能力清单：配置(脱敏)、阶段、任务类型、provider、插件加载错误 —— **前端菜单就是读它** |
| GET | `/api/events` | SSE：`progress` / `log` / `job` 事件 |
| GET | `/api/projects` | 项目列表（带最近任务、产出 key） |
| POST | `/api/projects` | 新建：`source_type=url/file/empty` + `auto_start` + `job_kind` |
| POST | `/api/projects/upload` | 上传文件（multipart，流式写盘） |
| GET/PATCH/DELETE | `/api/projects/{pid}` | 详情 / 改名 / 删除（连带删文件） |
| POST | `/api/projects/{pid}/jobs` | 对已有项目发起任务（换模型重跑等） |
| GET | `/api/jobs?project_id=` | 任务列表 |
| GET | `/api/jobs/{jid}` | 任务详情 + 日志 |
| POST | `/api/jobs/{jid}/cancel` | 取消 |
| GET | `/api/projects/{pid}/raw` | 视频流（**自己实现了 Range**，浏览器可拖动、秒开） |
| GET | `/api/projects/{pid}/artifact/{key}` | 任意产出文件（`?download=true` 下载） |
| GET | `/api/projects/{pid}/segments?q=&start=&end=` | 句子查询/搜索 |
| GET | `/api/projects/{pid}/transcript` | 转写 JSON |
| GET | `/api/projects/{pid}/notes` | `{markdown, summary}` |
| GET | `/api/projects/{pid}/frames` · `/frames/{idx}/image` | 关键帧列表（含 VLM 描述）/ 单张图 |
| POST/GET | `/api/projects/{pid}/clips` | 收藏句子进句库（自动切音频/视频切片） |
| GET | `/api/clips` · `PATCH/DELETE /api/clips/{cid}` | 跨项目句库 / 改标签备注 / 移除 |
| POST | `/api/clips/{cid}/review` | 复习 +1 |
| GET | `/api/clips/{cid}/media?kind=audio|video` | 句子切片播放 |
| POST | `/api/projects/{pid}/clips/export` | 导出 Anki 牌组（`.apkg`，没装 genanki 就退化成 TSV zip） |
| POST | `/api/projects/{pid}/score` | **发音评分**：上传录音（webm/mp4）→ 分数 + 逐词明细 + 音素级诊断（`phonemes` 字段） |
| GET | `/api/scores/{sid}/recording` | 回放你自己的录音 |
| GET | `/api/projects/{pid}/scores` | 评分历史 + 每句最高分 |
| GET | `/api/projects/{pid}/segments/{idx}/audio` | **单句音频**：按需切片并缓存（"只听这一句"、复习卡播放都用它） |
| GET | `/api/projects/{pid}/reviews` | 该项目的今日复习队列 + 计数 + 未来负载 |
| GET | `/api/reviews/due` · `/api/reviews` | 跨项目今日队列 / 全部队列项 |
| POST | `/api/reviews/sync` | 把历史低分句和收藏句补进队列（幂等） |
| POST | `/api/projects/{pid}/reviews` | 手动把某句加入复习 |
| POST | `/api/reviews/{rid}/grade|snooze|suspend|reset` | 评分推进调度 / 明天再说 / 暂停 / 重置 |
| GET/POST | `/api/reviews/words` · `/api/reviews/words/sync` | 单词卡队列 / 把历史练砸的词补进队列（幂等） |
| POST | `/api/reviews/words/{wrid}/grade\|snooze\|suspend` · DELETE `/api/reviews/words/{wrid}` | 单词卡的调度操作（复习页单词卡用） |
| GET | `/api/analytics?project_id=&days=` | 训练统计全量（趋势/分布/高频漏词/高频错音/最差句子/复习负载） |
| GET | `/api/words/pairs` · `/groups` · `/plan` | 最小对立对词库 / 分组 / 按你的错音排出来的训练计划 |
| GET | `/api/words/audio?word=&slow=` | espeak 合成单词音频（离线、缓存到 `data/tts/`） |
| POST | `/api/words/listen` | 听音辨词记账 |
| POST | `/api/words/say` | 跟读一个词 → 音素级判定（关键音读对没有） |
| POST | `/api/projects/{pid}/shadow/analyze` | **影子跟读分析**：覆盖率 / 平均滞后 / 逐句跟丢情况 |

> **「按说话人拆句」不是新接口**，而是 `POST /api/projects/{pid}/jobs` 的一个 `kind`
> （`split_speakers`）。因为它会重排句子编号，所以做成显式任务、由用户在「概览」页点，
> 而不是挂在 `diarize` 后面自动跑 —— 用户该知道自己在做什么。

---

## 5. 前端

- 无构建：浏览器直接跑 ES Module，`web/app.js` 是装配入口。
- **标签页注册表**：`VN.ui.registerTab({id, label, order, render(panel, ctx)})`，
  新功能文件只要 `import` 进来就会出现在导航栏。
- **事件总线**：`VN.on('projectChanged'|'segChanged'|'time'|'jobFinished'|'keydown', fn)`，
  所以「字幕列表点一下跳转播放器」不需要模块间互相引用（`player.js` 导出 `player.seekTo`）。
- 状态都在 `VN.store`（当前项目、句子、译文、播放选项），选项持久化在 localStorage。

---

## 6. 并发与性能

- **Worker 是单线程的**：ASR 会把 CPU 吃满，并行没有意义，还会互相拖慢。任务按 FIFO 顺序跑。
- 浏览器上传有进度条；SSE 让进度实时可见；任务可取消（Stage 在每个耗时循环里 `ctx.check_cancel()`）。
- CPU-only 的现实：`small` 模型大约 0.3~0.6x 实时（1 小时视频约 1.5~3 小时？实测 10 分钟视频约 1~3 分钟）。
  想快就换云端 ASR（`.env` 里 `VN_ASR_PROVIDER=cloud`）+ 云端大模型。
- 关键帧按 0.5s 采样 + 直方图差异检测，长视频设了 `max_frames` 上限，不会爆炸。
