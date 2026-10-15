# VideoNote · 视频学习工作台

把视频（本地文件或链接）丢进来，自动做 **语音识别 → 画面分析 → 摘要笔记 → 字幕**，
并且支持 **逐句跟读（说完一句自动暂停等你 repeat）** 的口语训练模式。

> 特点：**完全本地可跑**（不配任何 API key 也能用）、**插件化架构**（加功能不用改既有代码）、
> **像 App 一样用**（桌面图标双击即开，独立窗口，没有浏览器地址栏）。

---

## 1. 快速开始（推荐：桌面图标）

**双击桌面上的「VideoNote」图标。** 就这一步。

它做的事：后台服务没启动就自动启动 → 等就绪 → 打开一个独立的应用窗口。
关掉窗口不影响后台服务（下次打开秒开）；想彻底关掉就双击桌面的「停止 VideoNote」。

> 桌面图标丢了？运行一次：
> ```powershell
> powershell -ExecutionPolicy Bypass -File tools\install_shortcuts.ps1
> ```

**其他启动方式**

| 方式 | 说明 |
| --- | --- |
| `run.bat` | 带黑窗口启动，自动开系统浏览器（想看日志时用） |
| `start_server.bat` | 只启动后台服务，不占窗口，日志在 `data\server.log` |
| `stop_server.bat` | 停止后台服务 |
| 手动访问 | <http://127.0.0.1:8756/> |

**导入视频的三种方式**

1. **本地上传** —— 选文件上传（会复制一份到项目目录，大文件慢）
2. **本机路径** —— 点「📁 浏览」在网页里直接点着选，**不复制文件、立刻开始**（大视频首选）
3. **视频链接** —— B站 / YouTube 等 yt-dlp 支持的站点

---

## 2. 配置（`.env`）

`run.ps1` 首次运行会从 `.env.example` 建出 `.env`。填 key 有个小工具：

```powershell
.\.venv\Scripts\python.exe tools\set_key.py llm sk-你的key   # 写入并当场验证能不能用
```

| 作用 | 变量 | 说明 |
| --- | --- | --- |
| 摘要/笔记/翻译 | `VN_LLM_API_KEY` | **推荐 DeepSeek**：<https://platform.deepseek.com> 充 ¥10 够用很久 |
| 云端语音识别（可选） | `VN_ASR_API_KEY` | 硅基流动 <https://cloud.siliconflow.cn>，有免费额度，比本地快很多 |
| 本地语音识别模型 | `VN_WHISPER_MODEL` | `tiny` / `base` / `small` / `medium`，CPU 推荐 `small` |
| 切换云端识别 | `VN_ASR_PROVIDER` | `local`（默认）/ `cloud` / `auto` |
| 说话人分离 | `VN_DIARIZE_PROVIDER` | `local_ecapa`（默认，免费离线）/ `pyannote`（更准，需 HF token） |
| 已知人数更准 | `VN_DIARIZE_NUM_SPEAKERS` | `0`=自动判断；双人对话填 `2` |
| 画面描述 | `VN_VISION_PROVIDER=vlm` + `VN_VLM_*` | 视觉模型（硅基流动 Qwen2.5-VL 等），按帧计费，用 `VN_VLM_MAX_FRAMES` 控成本 |
| 画面文字 OCR | `VN_ENABLE_OCR` | 需要 `pip install rapidocr-onnxruntime` |

**没配 key 会怎样？** 语音识别、说话人分离、发音评分、跟读、字幕全都照跑（都是本地的）；
只有「摘要笔记」会退化成关键词排序，「翻译」和「画面描述」会跳过。

---
## 3. 能做什么

| 功能 | 怎么做 |
| --- | --- |
| 摘要 + 笔记 | 导入后跑「完整分析」，笔记页可下载 `.md` |
| 自动字幕 | 「转写 + 字幕」→ 得 SRT/VTT，可下载 / 可压制成硬字幕 mp4 |
| 双语字幕（美剧学习） | 「字幕（含翻译，双语）」，需要 LLM key |
| 画面分析 | 关键帧画廊，点图跳到对应时间；开启 OCR 后能读屏幕上的字 |
| **画面描述（视觉大模型）** | 把关键帧交给 Qwen2.5-VL 之类的视觉模型，板书/PPT/代码写进笔记；需在 `.env` 开 `VN_VISION_PROVIDER=vlm` |
| **说话人分离** | 「说话人分离」任务：本地声纹模型标出每句话是谁说的，可按人筛选台词（美剧/访谈必备），零 API |
| **跟读句库** | 「逐句转写」里点 ☆ 收藏 → 「跟读句库」页统一管理（标签/备注/复习次数/播放切片） |
| **Anki 导出** | 句库页「导出 Anki 牌组」→ 生成 `.apkg`（卡片带音频），导入 Anki 手机端接着练 |
| **发音评分** | 播放页或句库页：录一句 → 「🎯 评分」→ 总分 + 准确/完整/流利三维 + **逐词标色**（绿对/黄读错/红漏读/紫多说）+ 改进建议 |
| **逐句跟读（Repeat）** | 播放器勾「说完一句自动暂停」，每句结束停下等你读；`R` 重听、`M` 录音、AB 对比 |
| **影子跟读（Shadowing）** | 播放器「🎧 开始影子跟读」：原声不停，你滞后约 1 秒跟着说；停下来算出覆盖率、平均滞后、跟丢的句子 |
| **⭐ 音素级发音评测** | 读错的词会继续深挖到**音标**：`/θ/ 发成了 /s/`、`/v/ 发成了 /w/`、长短元音混了……每种错误都配一句"嘴该怎么放"。纯本地，零 API |
| **⭐ 单词最小对立对** | 「单词」页：ship/sheep、think/sink 这种**只差一个音**的词对。两种练法：🎧 听音辨词（练耳朵）、🎙 跟读对比（练嘴，判定到关键音）。参考音由 espeak 现场合成，离线免费 |
| **⭐ 复习队列（间隔重复）** | 读错的句子、你收藏的句子**自动排队**；SM-2 决定什么时候再练（明天 → 3 天 → 8 天…），「复习」页一次只面对一句 |
| **⭐ 训练统计** | 「统计」页：每天练了多少、分数趋势、连续打卡、**最常漏的词**、**最常读错的音**、最该重练的句子、未来 14 天复习量 |
| 视频链接 | 支持 B站 / YouTube 等 yt-dlp 支持的站点 |

快捷键：`空格` 播放/暂停、`R` 重听本句、`N/→` 下一句、`P/←` 上一句、`M` 录音、`L` 单句循环。
「复习」页另有：`空格` 听原声、`R` 开始/停止录音（停止后自动评分）、`Enter` 下一句。

### 一条完整的口语训练链路

```
挑句子(☆)  →  句库  →  🔊听原声 → 🎙录音跟读 → 🎯评分看差在哪个词
                            ↑                          │
                            └──── 重录直到 85+ ─────────┘
                                        │
                        读错的句子自动进「复习」队列（SM-2 间隔重复）
                                        │
                         统计页看趋势 / 高频错音 / 导出 Anki 牌组
```

### 发音评分是怎么算的（本地、不花钱）

**第一层：词级**（衡量「别人能不能听懂你」）

把你的录音**再喂给同一个 Whisper**，看它"听成了什么"，再和原句做逐词对齐：

| 维度 | 含义 | 权重 |
| --- | --- | --- |
| 准确 accuracy | 念对了多少（多说的词会扣分） | 0.60 |
| 完整 completeness | 原句有多少词被念出来 | 0.15 |
| 流利 fluency | 时长比 vs 原句 + 长停顿次数 | 0.25 |

**第二层：音素级**（衡量「哪个音发错了」）

对第一层判定为「读错」或「读得含糊」的词，切出你在录音里说它的那一小段，
喂给 wav2vec2 音素模型（`facebook/wav2vec2-lv-60-espeak-cv-ft`，1.2GB，本地跑）：

1. 文本 → IPA 音标：`espeak-ng`（和模型训练标签同一套符号），失败时退到 CMU 发音词典；
2. 你的音频 → 每 20ms 一个音素后验概率（CTC）；
3. **强制对齐**（2N+1 状态 Viterbi）：把"该发的音"对齐到帧上，拿到每个音素的帧区间；
4. **GOP 打分**：`log p(该音素) − log p(最强的音素)`，越接近 0 说明发得越准；
5. 判定 正确 / 含糊 / 读错（附「听成了什么」）/ 漏读 / 多读，并给出针对性的发音要点。

实测：JFK 演讲片段（真人语音 10.8 秒，68 个音素）准确识别 **97%**，推理 **4 秒**（RTF 0.38，纯 CPU）。
只对可疑词推理是刻意的设计——整句都跑音素模型会明显更慢，而收益很小。

> 记法等价规则：`Xɹ ↔ X`、长音符号 `ː` 有无、`ə ↔ ɚ` 视为同一个音，避免因为标记差异误判用户读错。

---

## 4. 项目结构

```
videonote/
├─ videonote/              后端（Python / FastAPI）
│  ├─ config.py            .env 配置
│  ├─ db.py                SQLite：projects / artifacts / segments / jobs / clips / scores
│  ├─ models.py            领域模型
│  ├─ core/
│  │  ├─ registry.py       ★ 插件注册中心（Stage / Provider / Router / JobKind）
│  │  ├─ stages.py         ★ Stage 基类 + 上下文 + 管线执行器
│  │  ├─ jobs.py           后台任务工人（单线程顺序跑，ASR 吃满 CPU）
│  │  ├─ media.py          ffmpeg / PyAV 媒体工具
│  │  ├─ audio.py          音频解码 / 读波形 / 波形峰值
│  │  ├─ scoring.py        ★ 发音评分（逐词对齐 + 三维打分）
│  │  └─ events.py         SSE 事件总线（实时进度）
│  ├─ providers/           ★ 可替换实现：ASR(本地/云端)、LLM、视觉、说话人分离
│  ├─ pipeline/            ★ 处理阶段：ingest/audio/asr/frames/captions/notes/subtitles/translate/diarize
│  │  └─ jobkinds.py       ★ 任务类型 = 阶段组合（前端下拉框读这里）
│  └─ api/                 HTTP 接口（routes_*.py 会被自动发现）
│     ├─ routes_clips.py   ★ 跟读句库 + Anki 导出
│     └─ routes_practice.py★ 发音评分 + 影子跟读分析
├─ web/                    前端（原生 ES Module，无需构建）
│  ├─ app.js               启动装配
│  ├─ js/core.js           API 客户端 / 全局状态 / 标签页注册表 / 迷你 Markdown
│  ├─ js/recorder.js       录音组件（MediaRecorder + 音量条 + 上传）
│  └─ js/features/*.js     ★ 一个文件 = 一个界面功能，自注册标签页
├─ tools/                  自检与辅助脚本（见下）
├─ samples/                测试素材
└─ data/                   运行时数据（数据库 / 媒体 / 模型 / 产出），已 gitignore
```

### 自检脚本（改完代码建议跑一遍）

```powershell
.\.venv\Scripts\python.exe tools\status.py           # 一眼看服务/项目/产出状态
.\.venv\Scripts\python.exe tools\smoke_test.py       # 全流程：导入→识别→关键帧→笔记→字幕
.\.venv\Scripts\python.exe tools\test_llm_pipeline.py# 大模型摘要 + 双语字幕（需要 key）
.\.venv\Scripts\python.exe tools\test_practice.py    # 句库 / 切片 / Anki 导出 / 发音评分 / 影子跟读
.\.venv\Scripts\python.exe tools\test_diarize.py     # 说话人分离（自动合成双人音频验证）
.\.venv\Scripts\python.exe tools\test_captions.py    # 画面描述（自带假视觉模型，不需要 key）
.\.venv\Scripts\python.exe tools\test_fs_browser.py  # 内置文件浏览器
.\.venv\Scripts\python.exe tools\demo_setup.py       # 一键准备演示项目
.\.venv\Scripts\python.exe tools\probe_media.py 视频 # 看有没有音轨/多长/分辨率
.\.venv\Scripts\python.exe tools\clean_projects.py   # 清理重复项目
```

### 界面小技巧

- 左侧项目名 **双击可改名**，**右键可删除**（连文件一起删）
- 导入「本机路径」时点 **📁 浏览**，在网页里点着选文件，不用手敲路径
- 导入弹窗按 **ESC** 或点深色空白处都能关
- 音频没声音 / 识别报错？先用 `tools\probe_media.py` 确认视频有没有音轨

管线本质是**产出物（artifact）的接力**：

```
ingest → media
  ├─ audio → audio → asr → transcript → notes / subtitles / translate
  └─ frames → 关键帧 + OCR
```

每个阶段都可以单独重跑（`force`），已有产出会自动跳过。

---

## 5. 加新功能？

见 [`docs/PLUGIN_GUIDE.md`](docs/PLUGIN_GUIDE.md)。三步：写 Stage → 串 JobKind → （可选）加界面。
后端自动发现插件，前端下拉框自动出现新任务类型。

---

## 6. 常见问题

| 问题 | 解决 |
| --- | --- |
| 首次识别很慢 | 第一次要下载 Whisper 模型（`small` 约 500MB）。国内已默认走 `hf-mirror.com` 镜像 |
| 报「找不到 ffmpeg」 | 已经用 `imageio-ffmpeg` 自带二进制，无需手动安装；如仍报错，在 `.env` 设 `VN_FFMPEG=路径` |
| B站/YouTube 下载失败 | 大多是风控，导出浏览器 cookies 为 `cookies.txt`，`.env` 里设 `VN_YTDLP_COOKIES=绝对路径` |
| 摘要质量差 | 没配 `VN_LLM_API_KEY`，正在用本地抽取式摘要 |
| 识别不准 | 换 `VN_WHISPER_MODEL=medium`（慢 2-3 倍），或改用云端 ASR |
| 服务端口冲突 | `.env` 改 `VN_PORT` |
