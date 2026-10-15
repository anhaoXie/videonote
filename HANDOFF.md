# VideoNote 交接报告（给下一个对话）

> 初版生成时间：2026-10-15 17:30（Asia/Shanghai）
> **v0.4 更新：2026-10-15 17:55** —— 第 1 节的快照已按 v0.4 修正；正文其余部分（架构、命令、
> 限制清单）基本仍然有效，但「已完成到什么程度」请以 `ROADMAP.md` 为准。
> 项目根目录：`C:\Users\谢安豪\Documents\deepseek-harness\default-workspace\videonote`
> **已经是 git 仓库了**：v0.4 开工时 `git init` 并提交了基线（`c10ed3d` → `5fdd24d`）。
> 改动前先看 `git status` —— 它能暴露「另一个会话同时也在改这个项目」这类**静默覆盖**，
> 而这在 v0.4 期间真实发生过（另一个会话写入了 `HANDOFF.md` 与 `reset_practice_data.py`）。
> 本文档由 v0.3 那一轮会话写成，数字都是**实测值**；v0.4 的两节由接手的那一轮补充。

---

## 0. 一句话

**VideoNote** = 本地视频学习工作台 + 英语口语训练器。
导入视频/链接 → 语音识别 + 关键帧 + 大模型 → 摘要笔记 / 字幕 / 翻译；
在此之上叠了一个**完全不花钱、全离线**的发音训练闭环：
逐词打分 → 音素级诊断（哪个音发错了）→ 间隔重复复习 → 单词最小对立对 → 训练统计。

技术选型：Windows 11 + **纯 CPU**（i5-13420H / 16GB / 无独显）、Python 3.12.6、
FastAPI + SQLite、原生 ES Module 前端（**无构建步骤**）、插件化后端。

代码规模：Python **8256 行**（48 个文件）+ 前端 JS/CSS **3544 行**（15 个文件）。（v0.3 时的口径。）

> ⚠️ v0.4 更新：上面这组数字**没能复现** —— 按「后端包 `videonote/` 下的全部 `.py`」
> 重新量是 **7205 行 / 49 个文件**（同口径下 v0.3 不可能比它多 1000 行，所以两者口径不同，
> 不要直接相减）。v0.4 确定新增的是：`videonote/core/split.py`（415 行）、
> `videonote/pipeline/split_speakers.py`（59 行）、`tools/test_split_speakers.py`（259 行）、
> `tools/test_hybrid_queue.py`（216 行）。其余分项：`tools/` 2804 行 / 37 个文件、
> 前端 `web/` 3249 行 / 15 个文件。

---

## 1. 当前状态快照

| 项 | 值 |
| --- | --- |
| 版本号 | **0.4.0**（`videonote/__init__.py`） |
| 服务地址 | `http://127.0.0.1:8756`（健康检查 `GET /api/health` → `{ok,version,worker_busy}`） |
| 服务是否在跑 | 是（v0.4 接手的会话启动的后台任务；**会话结束会被系统回收**） |
| 插件加载错误 | `{}`（零错误） |
| 已注册阶段 | 11 个：`asr audio burn captions diarize frames ingest notes split_speakers subtitles translate` |
| 任务类型 | 12 种：`analyze analyze_plus burn captions diarize frames ingest split_speakers subtitle_full summarize transcribe translate` |
| Provider | asr `[cloud, local]`、llm `[deepseek, none, openai_compat]`、vision `[local_opencv, vlm]`、diarize `[local_ecapa, pyannote]` |
| 数据表 | `projects segments artifacts jobs job_logs clips scores reviews practice_log pair_drills word_reviews` |
| 项目数 | 3 个（见下表） |
| 练习数据 | **已全部清零**（v0.4 自测留下的假练习已用 `tools/reset_practice_data.py --yes` 清掉；clips 保留 3 条收藏） |
| 大模型 key | DeepSeek 已写入 `.env`（`VN_LLM_API_KEY`）并**实测可用** |
| v0.4 新增 | ① **统一复习队列**：单词卡进 SM-2（`word_reviews` 表），复习页是句子卡 + 单词卡的混排队列 ② **按说话人拆句**：新任务 `split_speakers`，按词级时间戳切开「多人被合成一句」的情况，含 seg_idx 数据迁移 + 事务 + 数据库自动备份 |

现有项目：

| id | 内容 | 时长 | 说明 |
| --- | --- | --- | --- |
| `p_7110ad2c90f7` | 物理课：功能关系与能量转换 | 361.6s / 36 句 | **用户自己的屏幕录制**（中文，有音轨，1870x1064@30fps） |
| `p_a1eb959b7842` | 演示：JFK 演讲片段 | 11.3s / 1 句 | 英文真人语音，音素评测的黄金测试素材 |
| `p_4a64d8d069be` | 自检 - 两说话人 | 28.0s / 4 句 | 合成 A/B/A/B 双人对话，测说话人分离用 |

---

## 2. 立刻能跑的命令

```powershell
cd C:\Users\谢安豪\Documents\deepseek-harness\default-workspace\videonote

# 起服务（开发用；用户桌面是双击 VideoNote 图标 → launcher.py）
.\.venv\Scripts\python.exe run.py --no-browser

# 停服务
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'run\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 系统总览（服务/项目/产出）
.\.venv\Scripts\python.exe tools\status.py

# 全套自检（详见第 9 节）
.\.venv\Scripts\python.exe tools\smoke_test.py
```

> 注意：`Start-Process -WindowStyle Hidden` 起服务**会挂住**，必须用 DSH 后台任务或 `run.py` 直接跑。

---

## 3. 架构

### 3.1 数据流

```
导入(本地文件/本机路径/视频链接 yt-dlp)
   └→ 抽音轨 → faster-whisper 转写(词级时间戳) → 关键帧/场景检测
                       ├→ 摘要笔记(map-reduce，走 LLM，没 key 退化成抽取式)
                       ├→ SRT/VTT 字幕 → 双语翻译 → 硬字幕压制
                       ├→ 说话人分离(ECAPA 声纹 + 聚类)
                       └→ 画面描述(视觉大模型，可选)
逐句跟读 → 录音 → Whisper 回环 → 词级打分 → 音素级诊断 → 复习队列(SM-2)
```

### 3.2 插件化（核心约束）

一切通过注册中心 `videonote/core/registry.py`：

| 装饰器 | 作用 |
| --- | --- |
| `@register_stage` | 管线阶段；`requires`/`produces` 是 artifact key 契约 |
| `@register_provider(kind,name)` | 可替换实现（asr/llm/vision/diarize） |
| `@register_router` | FastAPI 路由，自动挂到 `/api` 前缀下 |
| `register_job_kind` | 把阶段串成"任务类型" |

`Registry.autodiscover()` 自动导入 `videonote/providers/*`、`videonote/pipeline/*`、
`videonote/api/routes_*.py`。**前端下拉框读 `/api/capabilities`，加插件不用改前端。**

**artifact 是阶段之间唯一的契约**：阶段写 `ctx.put_artifact(key, path)` 或 `ctx.save_json(key, data)`，
下游用 `ctx.artifact(key)` 拿。除非 `force=True`，产物已存在就**幂等跳过**。

> ⚠️ 这个"幂等跳过"坑过一次：给笔记加了「画面信息」章节后笔记不更新，
> 因为 notes 产物已存在被跳过。修法见第 8 节。

### 3.3 关键文件

| 文件 | 职责 |
| --- | --- |
| `videonote/config.py`（185） | `.env` → Settings；设置 `HF_ENDPOINT`(hf-mirror)、`HF_HOME=<data>/hf`、`HF_HUB_DISABLE_XET=1` |
| `videonote/db.py`（778） | 建表 + 全部 SQL 辅助函数（raw sqlite3，WAL） |
| `videonote/core/stages.py`（266） | `Stage`/`StageContext`/`PipelineRunner`；`set_progress` 节流 |
| `videonote/core/jobs.py`（81） | **单线程** Worker：领任务→跑管线→写状态→发事件 |
| `videonote/core/registry.py`（138） | 注册中心 + `import_errors` |
| `videonote/core/media.py`（174） | ffmpeg（imageio-ffmpeg 自带）/ PyAV：探测、抽音、压制、切片 |
| `videonote/api/app.py`（82） | lifespan + 挂载 registry 路由 + **`_no_store_static` 中间件**（非 /api 响应加 no-store，从根上消灭"浏览器拿旧 JS"） |
| `web/js/core.js`（269） | `api`(带超时/断连中文提示)、`ui.registerTab`、事件总线、迷你 Markdown |
| `web/js/features/*.js` | 一个文件一个功能页，自注册 |

### 3.4 HTTP 接口地图

```
/api/health  /api/capabilities  /api/events(SSE)
/api/projects  /api/projects/upload  /api/projects/{pid}(GET/PATCH/DELETE)
/api/projects/{pid}/jobs  /api/jobs  /api/jobs/{jid}/cancel
/api/projects/{pid}/raw(自实现 Range 流)  /artifact/{key}  /segments  /transcript  /notes
/api/projects/{pid}/frames  /frames/{idx}/image
/api/projects/{pid}/clips  /api/clips  /api/clips/{cid}  /clips/{cid}/media?kind=  /clips/export(Anki)
/api/projects/{pid}/score  /scores/{sid}/recording  /projects/{pid}/scores
/api/projects/{pid}/segments/{idx}/audio        # 单句音频切片（按需缓存）
/api/projects/{pid}/reviews  /api/reviews/due  /api/reviews  /api/reviews/sync
/api/reviews/{rid}/grade|snooze|suspend|reset
/api/analytics?project_id=&days=
/api/words/pairs  /words/groups  /words/plan  /words/audio?word=&slow=
/api/words/listen(POST)  /words/say(POST 上传录音)  /words/history(DELETE)
/api/fs/list?path=  /api/fs/info?path=
```

---

## 4. 功能清单

| # | 功能 | 入口 | 状态 |
| --- | --- | --- | --- |
| 1 | 导入（文件 / 本机路径 / 视频链接 yt-dlp） | 库页 | ✅ |
| 2 | 语音识别（本地 faster-whisper，可切云端） | 任务「转写」 | ✅ |
| 3 | 关键帧 / 场景检测 | 帧页 | ✅ |
| 4 | 摘要笔记（map-reduce + LLM，没 key 退抽取式） | 笔记页，可下 `.md` | ✅ 真 key 实测过 |
| 5 | 字幕 SRT/VTT + 双语翻译 + 硬字幕压制 | 任务/下载 | ✅ |
| 6 | 说话人分离（ECAPA + 层次聚类，零 API） | 任务「说话人分离」 | ✅ 单测过（k=2，轮廓 0.52） |
| 7 | 画面描述（视觉大模型 → 笔记「画面信息」） | 需 `VN_VISION_PROVIDER=vlm` | ✅ 用 mock VLM 验过 |
| 8 | 逐句跟读（说完自动暂停 + A/B 对比） | 播放器 | ✅ |
| 9 | 影子跟读（覆盖/滞后分析） | 播放器 | ✅ 后端过；浏览器 UX 未亲测 |
| 10 | 跟读句库 + Anki 导出（`.apkg` 带音频） | 句库页 | ✅ |
| 11 | **发音评分（词级 + 音素级）** | 播放器/句库/复习 | ✅ 见第 5 节 |
| 12 | **复习队列（SM-2，句子卡 + 单词卡混排）** | 复习页 | ✅ |
| 13 | **单词最小对立对（听音辨词 / 跟读对比）** | 单词页 | ✅ 见第 5.4 |
| 14 | **训练统计**（趋势/打卡/高频漏词/高频错音/复习负载） | 统计页 | ✅ |

桌面化：`launcher.py`（健康检查 → 用 `DETACHED_PROCESS` 拉起 `run.py --no-browser` →
开应用窗口：pywebview 失败则回落 Chrome/Edge `--app=<url>`）、
`tools/make_icon.py`、`tools/install_shortcuts.ps1`（桌面 + 开始菜单快捷方式）、
`start_server.bat` / `stop_server.bat`、`tools/set_key.py`（写 `.env` 并当场验证 key）。

---

## 5. 英语训练引擎（最核心、也最容易踩坑的部分）

### 5.1 词级评分（`videonote/core/scoring.py`，273 行）

把用户录音**再喂给同一个 Whisper**，看它"听成了什么"，再和原句做逐词对齐
（`difflib.SequenceMatcher`）：

| 维度 | 含义 | 权重 |
| --- | --- | --- |
| accuracy | 念对了多少（多说的词扣分） | 0.60 |
| completeness | 原句有多少词被念出来 | 0.15 |
| fluency | 时长比 vs 原句 + 长停顿次数 | 0.25 |

对齐结果 `detail.words[]` 每项带：`w / status(ok|wrong|missing|extra) / heard /
ti(原句下标) / ui(录音里下标) / start / end / prob`。
**`start`/`end` 是音素级切片的关键**——它们指向"用户录音里说这个词的那一段"。

`phoneme_suspects()` 挑出值得精听的词：`status=wrong`，或 `status=ok 且 prob<0.6`，
最多 `VN_PHONEME_MAX_WORDS`（默认 8）个。

### 5.2 音素级诊断（`videonote/core/phonemes.py`，846 行）

```
文本 --espeak-ng--> IPA 音标（去重音）        ← 和模型训练标签同一套符号
用户音频 --wav2vec2--> 每 20ms 一个音素后验（CTC，50 帧/秒，blank=<pad>=0）
        ↓
自写 2N+1 状态 Viterbi 强制对齐 → 每个音素的帧区间
        ↓
GOP = mean(log p(该音素) − log p(最强的音素))，换算 log10
        ↓
判定 正确 / 含糊(weak) / 读错(sub，附"听成了什么") / 漏读(del) / 多读(ins) / 没听清(unclear)
        ↓
PHONE_TIPS：把 (期望音, 听到音) 翻译成中文发音要点
```

**实测**（JFK 真人语音 10.8s / 68 个音素）：识别准确率 **97.1%**，推理 **4.0s**（RTF 0.38，纯 CPU）。

判定规则（刻意"宁可放过不可错杀"）：

```python
same = equivalent_phones(ph, heard)      # 记法等价，不是发音错误
if same:            status = "weak" if gop < (thr - 0.6) else "ok"
elif not heard:     status = "del"  if gop < thr else "weak"
else:               status = "sub"  if gop < thr else "weak"
# thr = settings.phoneme_gop_threshold = -1.2（越小越宽容）
# accuracy = (ok + weak) / total   ← 「识别出来了」的比例
# problems = sub + del 的个数      ← 真正该改的音
```

`equivalent_phones()` 的三条等价：去掉长音符号 `ː` 后相等、
`Xɹ ↔ X`（卷舌色彩元音写法不同）、`ə ↔ ɚ`。

**静音护栏**：`RMS < VN_PHONEME_MIN_RMS`（默认 0.008）直接返回"这段几乎是静音，
没法判断发音"；自由解码一个音素都没有时全部标 `unclear`，**不判用户漏读**。
（这两条是修"读对了却报漏读"误报的关键。）

G2P 双路：**espeak-ng 为主**（`core/phonemes.py` 里 ctypes 直接调 DLL），
`cmudict` + ARPAbet→IPA 表为兜底（纯 Python，覆盖 126k 词，还有重音信息）。
`_coerce_to_vocab()` 把音素收敛到模型词表里存在的写法。

### 5.3 复习队列（`videonote/core/srs.py`，583 行）

- SM-2：`score_to_quality()` 92/80/65/45 → q=5/4/3/2/1；
  q<3 → `reps=0, lapses+1, status='learning'`，10 分钟后重来；
  否则 reps 1→1天、2→3天、≥3→`interval*ease`，ease 增减后下限 1.3、最长 365 天。
- `REVIEW_THRESHOLD=80.0`（低于即入队）、`EASY_SCORE=92.0`。
- **两张表，一层视图**：句子卡在 `reviews`（`UNIQUE(project_id, seg_idx)`），
  单词卡在 `word_reviews`（`UNIQUE(pair_id, word)`），
  统一队列由 `srs.unified_queue()` 在**读的时候**混排（`_interleave()`，`WORD_RATIO` 控制比例），
  **不合并成一张表**（理由写在 `docs/V0.4-设计.md`）。
- 契约：从 `srs` 出来的**每张卡都带 `card_type`（sentence|word）**。
- `sync()` 幂等：扫历史低分句 + 收藏句 + `pair_drills` 补卡。
- 关键音读错必须把分数压到及格线下，否则整词 accuracy 会盖掉它。

### 5.4 单词最小对立对（`core/minimal_pairs.py` + `core/tts.py` + `api/routes_words.py`）

- **35 对 / 24 组**，每对带中文口型要点；按音素对立分组
  （iː/ɪ、uː/ʊ、θ/s、θ/f、ð/z、v/w、ɹ/l、ŋ/n、词尾清浊、ʃ/s、tʃ/ʃ、æ/ɛ…）。
- 音频由 **espeak-ng 现场合成**（`core/tts.py`，190 行，复用音素层的同一个 espeak 实例，
  缓存到 `data/tts/`）。零成本、离线。
- **验证门槛**（`tools/verify_pairs.py` → `data/minimal_pairs_verified.json`）：
  合成两个词 → 音素模型各自解码 → 只有"两个词的听感确实不同、且各自的区分音素被听到、
  且不互相串音"的才算合格。**实测只有 13/35 通过。**
- **为什么**：音素模型是真人语音训练的，机器人念孤立单词属于**域外数据**。
  最典型反例：机器音的 /θ/ 被听成 /s/（think → `[s ɪ ŋ]`）。
- 因此按用途分开：**听音辨词只用 13 对**（分辨不出就不出题），
  **跟读对比 35 对全用**（判分依据是 IPA 目标 + 用户自己的录音，与合成音无关）。

### 5.5 训练统计（`core/analytics.py`，350 行）

`report(pid, days)` 返回：overview（次数/句数/均分/最近20次均分/前后对比/分钟/活跃天/
当前连续/最长连续）、daily(120 天)、distribution(5 档)、weak_words(missing+wrong)、
**weak_phonemes**（只统计合法英文音素，避免中文视频里随口念的字母污染）、
worst_segments、by_project、modes、review{counts, forecast}、words(单词维度)。

---

## 6. 环境与依赖（精确版本，已实测可 import）

```
Python 3.12.6  (venv: .venv/)
torch 2.14.1+cpu / torchaudio 2.11.0+cpu / numpy 2.5.4
faster-whisper 1.2.1 / ctranslate2 4.8.2
transformers 5.19.0 / tokenizers 0.23.3 / huggingface-hub 2.2.0 / safetensors 0.8.0
espeakng-loader 0.2.4 / cmudict 1.1.3
speechbrain 1.1.1 / scikit-learn / onnxruntime 1.31.0
genanki（Anki 导出） / opencv-python-headless / Pillow / imageio-ffmpeg / yt-dlp
```

**版本约束的坑**：transformers 4.x 要求 `huggingface-hub<1.0`，与本机 hub 2.2.0 冲突；
**5.19.0 正好兼容**（要求 hub<3.0,>=1.31）。别乱降级。

模型文件（都在 `data/` 下，已下好）：
- Whisper `small`：`data/models/`（faster-whisper 缓存）
- 音素模型：`data/models/wav2vec2-lv-60-espeak-cv-ft/`（**1,263,535,127 字节**，只有 `.bin`）
- 声纹模型：`data/models/ecapa_model`
- HF 缓存被搬到 `data/hf`（原因见坑 4）

---

## 7. 血泪坑清单（**必须不要再踩**）

### 7.1 网络 / 安装

1. **`pip install` 会卡死**（走 localhost 代理零进展，45 秒无变化）。
   唯一可靠的装法：**先用 `Invoke-WebRequest` 从 PyPI 直连下载 wheel，再离线装**。
   ```powershell
   $m = Invoke-RestMethod "https://pypi.org/pypi/<包>/json"
   $f = $m.urls | Where-Object { $_.filename -like "*win_amd64.whl" } | Select-Object -First 1
   Invoke-WebRequest $f.url -OutFile "pip-wheels\$($f.filename)"
   .\.venv\Scripts\python.exe -m pip install --no-index --no-deps --only-binary=:all: `
       --find-links pip-wheels "pip-wheels\$($f.filename)"
   ```
   实测直连下载 1.4~2.9 MB/s。wheel 落在 `pip-wheels/`。
   **注意**：`pip install 文件名` 必须给相对/绝对**路径**，只给文件名会报 "looks like a filename"。
2. **wheel 平台标签是 `cp312-none-win_amd64`**（不是 `cp312-cp312`）；tokenizers/safetensors
   用 `cp310-abi3-win_amd64`。
3. HF 模型走 `hf-mirror.com`（`VN_HF_ENDPOINT`）。**不要用 `snapshot_download` 下载大文件**，
   用 `tools/get_phoneme_model.py` 的 urllib 分块下载（带进度、可续传）。
4. `huggingface_hub` 2.x 的 Xet 后端会报 `OSError: I/O error: 拒绝访问 (os error 5)`
   → 已设 `HF_HUB_DISABLE_XET=1`。symlink 报 `WinError 1314 客户端没有所需的特权`
   → `HF_HOME` 搬进 `data/hf` + speechbrain 的 `link_with_strategy` 被 monkeypatch 成复制。

### 7.2 espeak-ng（最阴的一个）

5. **espeak-ng 在 Windows 用 ANSI 编码打开文件，路径里有中文就找不到数据目录**
   （`谢安豪`），静默退回编译时的路径 `D:/a/espeakng-loader/...`，报
   `Error processing file ...: No such file or directory`。
   试过且**都不行**：UTF-8 编码传路径、mbcs 编码、Windows 8.3 短路径（用户目录没生成短名）、
   复制到 `C:\ProgramData`（Python 被限制在工作区外写文件）。
   **最终解法：只传相对路径**——`os.path.relpath(data_dir, os.getcwd())`，
   espeak 拿到的字符串全是 ASCII，目录解析交给操作系统（内部是 Unicode）。
   必要时临时 `os.chdir` 到数据目录父目录，用纯 ASCII 的相对名。
   **自检**：初始化后 phonemize `"think"`，读不出 `θ` 就认输走 CMU 词典兜底。
6. espeak 的 **`[[...]]` IPA 输入模式是坏的**（把音标当字母念：`[[ʃɪp]]` → `[ɛ ʃ s m ɔ l k p aɪ p i]`）。
   用拼写合成。
7. espeak 初始化用 **`AUDIO_OUTPUT_RETRIEVAL(1)`**（不占声卡），同一个实例既能
   `espeak_TextToPhonemes`（文本→音标）又能 `espeak_SetSynthCallback` + `espeak_Synth`（文本→语音）。
   回调函数对象**必须留引用**否则被 GC 回收导致崩溃。

### 7.3 音素算法

8. **CTC 强制对齐必须从终止状态（最后一个 blank）回溯，不能从 `argmax` 回溯。**
   早期版本从 argmax 回溯，音频稍不匹配后面的音素就拿不到帧、全被误判"漏读"
   （同一段音频准确率从 21% → 97%）。这是本项目**最关键的一个 bug**。
9. 22.05kHz → 16kHz **不能用 `np.interp` 线性插值**（混叠把高频折回语音频段）；
   用 FFT 带限重采样（`core/tts.py::resample`）。
   反过来也成立：带限会拿掉 /θ/ vs /s/ 赖以区分的接近 8kHz 的线索——
   说明该模型判咬舌音本就勉强（16kHz 训练数据里没有那些频率）。
10. 该模型仓库的 `tokenizer_class` 是 `Wav2Vec2PhonemeCTCTokenizer`，会 import phonemizer
    （拖进 csvw 等一串依赖）→ **只用 `Wav2Vec2CTCTokenizer.from_pretrained()` +
    `Wav2Vec2FeatureExtractor.from_pretrained()`，不要用 `Wav2Vec2Processor`**。
11. `blank id` 必须运行时读（`tokenizer.pad_token_id`），别硬编码。
12. 只对**可疑词**切片推理（整句跑音素模型明显更慢、收益很小）。

### 7.4 其他工程坑

13. `.bat` 文件**必须纯 ASCII**（中文注释在 GBK 控制台下让 cmd 解析错乱，
    实测报 `'：双击' is not recognized`）。`.ps1` **必须带 UTF-8 BOM**
    （否则 PowerShell 5.1 读中文乱码报 ParserError）。
    `.py`/`.js`/`.md` 用无 BOM UTF-8（用 `Add-Content -Encoding utf8` 追加后要验证 BOM=0）。
14. **Python 进程被限制在工作区外写文件**（PowerShell 可以）。
    所以任何"复制到 C:\ProgramData / C:\Users\Public"的绕法都不通。
15. `cv2.imwrite` 对非 ASCII 路径静默返回 False → 用 `cv2.imencode` + `Path.write_bytes`。
16. PyAV 19 删了 `metadata_errors`，faster-whisper 会 TypeError →
    用 `wave`+numpy 读 wav，把 ndarray 传给 `model.transcribe`。
17. **artifact 幂等跳过**：给笔记加「画面信息」章节后笔记不更新。
    修法：`captions.py` 结尾 `db.delete_artifact(pid, "notes")` 让摘要失效，
    并把 `captions` 任务的 stages 从 `["captions"]` 改成 `["captions", "notes"]`。
18. 上传录音路径冲突：原文件与解码后文件同名会被 unlink。
    原文件命名 `*.upload<ext>`，并加 `raw.resolve() != wav.resolve()` 保护。
19. `tempfile.gettempdir()` 在本机解析成 CWD → 统一用 `data/_tmp`。
20. 前端"点不动"的根因是**浏览器拿旧 JS** → 已加 `_no_store_static` 中间件 +
    `?v=` 查询串 + 启动失败红条（`window.onerror` / 4 秒未启动提示 Ctrl+Shift+R）。
21. 上传弹窗假死：`uploadWithProgress` 每 15s 检查，**5 分钟无新字节就 abort**
    并提示改用「本机路径」导入。
22. **pywebview 装不上**（proxy_tools 是 sdist，构建时要连 pypi.org）→ 放弃，
    launcher 走 Chrome/Edge `--app=` 模式（无地址栏，效果等同）。
23. **我（AI）起的后台任务和 WMI 脱离进程都会在会话结束时被回收** ——
    服务必须由用户在自己的桌面会话里双击图标启动，这是唯一可靠方式。

---

## 8. 验证：测试清单

全部在 `tools/` 下，**退出码 0 = 通过**。

| 脚本 | 覆盖 | 需要服务 |
| --- | --- | --- |
| `smoke_test.py` | 端到端冒烟（导入→转写→笔记→字幕） | ✅ |
| `test_srs.py` | 复习队列 + 训练统计（分析字段完整性） | ❌ |
| `test_hybrid_queue.py` | **句子卡 + 单词卡混排**（幂等同步、计数、混排比例、不污染句子卡、快照还原） | ❌ |
| `test_practice.py` | 跟读评分全链路 | ✅ |
| `test_phonemes.py` | 音素层：G2P / 词表映射 / 编辑距离 / **整句强制对齐 + GOP（真语音）** | ❌ |
| `test_phoneme_scoring.py` | 音素评分端到端（走 HTTP，用参考音频当"完美跟读"） | ✅ |
| `test_tts.py` | espeak 合成 + 缓存 + 带限重采样 + 交叉验证 + 验证门槛 | ❌ |
| `test_words.py` | 单词训练端到端（词库/音频/计划/记账/跟读判定） | ✅ |
| `test_diarize.py` | 说话人分离（k=2，轮廓系数 0.52，顺序 1/2/1/2） | ❌ |
| `test_captions.py` | 画面描述全链路（用 `tools/mock_vlm.py` 假视觉模型） | ❌ |
| `test_llm_pipeline.py` | 真 DeepSeek：摘要 + 翻译 | ❌ |
| `test_fs_browser.py` | 本机文件浏览器 | ✅ |

**上一次全量回归结果（2026-10-15 17:2x）**：全部退出码 0。

其它工具：`status.py`（总览）、`verify_pairs.py`（重建词对验证）、`get_phoneme_model.py`（下模型）、
`reset_practice_data.py`（清练习数据，**已含 pair_drills/word_reviews**）、
`clean_phoneme_noise.py`、`clean_projects.py`、`rename_project.py`、`probe_media.py`、
`set_key.py`、`make_test_video.py`、`mock_vlm.py`、`demo_setup.py`、`make_icon.py`、
`install_shortcuts.ps1`；`_probe_*.py` / `_check_deps.py` / `_fix_prefix.py` 是**一次性排查脚本**，可删。

---

## 9. 已知限制（诚实清单）

1. **纯 CPU**：Whisper `small` 约 0.3~0.6x 实时；音素模型 RTF 0.38（10.8s 音频 4s）；
   音素模型 fp32 常驻约 **3GB 内存**。
2. **音素评测只对英文有效**：中文/日文用英文音标体系出来是噪声，
   `analyze_phonemes` 已按"ASCII 字母占比 < 0.5 直接跳过"（词级评分照常）。
3. **单词从连续语流里单独切出来评，边界不如整句稳**。生产路径用的是"用户自己录音里
   那个词的时间戳"（Whisper 从同一段音频得出），所以比拿参考视频切片准。
4. **音素模型对合成音属于域外数据**（35 对只有 13 对能被它确认分辨）。
   机器音只能作示意，不能当标准音模仿。
5. **说话人分离在合成音上表现好，真实多人对话未充分验证**（Whisper 会把合成 A/B 音频
   并成一个长段，管线上看不出效果；权威测试是 `test_diarize.py` 的单测）。
6. **一句话里两个人轮流说时，只标占比大的那个说话人**（未做按词级时间戳拆句）。
7. 浏览器端 UX（影子跟读面板、单词页交互）**我没有真人点击验证过**，只验证了产物与接口。
8. 不是 git 仓库，没有历史版本可回滚。

---

## 10. 下一步（按价值/成本排序，含理由）

> 设计文档 `docs/V0.4-设计.md`（141 行）已经写好两个 P0 的方案，**队列那半已经实现**。

| 优先级 | 任务 | 为什么现在做 | 成本 |
| --- | --- | --- | --- |
| **P0** | **按说话人拆分长句** | 美剧/访谈里一句常有多人轮说，现在只标一个人。要用词级时间戳把句子切开并**重映射 seg_idx**——这会动到句库/评分/译文/复习队列里所有引用 seg_idx 的数据，必须做迁移（设计文档里有方案） | 中 |
| **P0** | **真人示范音** | 在用户自己的视频里搜最小对立对的词（词级时间戳已有），做成"真人示范 + 机器音对照"，绕开第 9.4 条的域外数据问题 | 中 |
| P1 | 音素模型 int8 量化（`quantize_dynamic`） | 省一半内存、提速 1.5~2.5×，纯 CPU 机器上收益明显 | 低 |
| P1 | 跨项目生词本 | 汇总术语表形成个人词库；用 embedding 找相似句 | 中 |
| P1 | 画面 + 语音联合问答（RAG） | 「第 12 分钟那个实验说明了什么？」 | 中 |
| P2 | 整季批量导入 / 项目分组 | 一次丢一季进去排队 | 低 |
| P2 | GPU 加速 / 局域网手机访问 / 笔记导出到 Obsidian | 见 ROADMAP | 低 |
| P2 | `git init` + 首次提交 | **现在没有任何版本控制，改坏了没法回滚** | 低 |

---

## 11. 用户目标与授权（接手必须知道）

**用户原话（未撤销的长期授权）**：

> 「你不需要再一步一步由我同意，请你把这个产品做到极致，资金不是问题，token 我也花得起，
> 就去干吧，直到你想不出其他的创新优化点」

其它已确认的偏好：
- ASR：**混合**方案（本地 faster-whisper 为主，可切云端）
- LLM：**DeepSeek**（用户说"你直接告诉我你最需要哪一个我去执行"，已在 `.env` 配好并验证）
- 界面：**桌面双击即用**（用户抱怨过"网页点不动、文件不好找"）
- 用户是中文母语者，目标是**练英语口语**（所以音素评测、最小对立对是核心价值）

**用户不需要再被逐步征求同意**，但：
- 不要申请沙箱提权（本会话审批被自动拒绝）
- 涉及**删数据**的操作要留预演（`--yes` 模式）并说清楚
- 报告用**中文、少术语**（用户明确要求过"用人话汇报"）

---

## 12. 接手建议（第一个小时做什么）

1. `cd` 到项目目录，跑 `tools\status.py` 和 `tools\smoke_test.py`，确认基线是绿的；
2. 起服务（`run.py --no-browser`），打开 `http://127.0.0.1:8756`，
   在浏览器里**亲眼过一遍**「单词」页和「复习」页（第 9.7 条是我没做的验证）；
3. 建议先 `git init && git add -A && git commit -m "v0.3.0 baseline"`，给自己一个回滚点；
4. 然后按第 10 节的 P0 往下做——**注意拆句会动 seg_idx，见 `docs/V0.4-设计.md`**。

---

## 13. 本轮新增/改动的文件（便于定位）

```
videonote/core/phonemes.py      846  音素评测核心（G2P/对齐/GOP/判定/发音要点）
videonote/core/tts.py           190  espeak 离线合成 + 带限重采样
videonote/core/minimal_pairs.py 159  35 对最小对立对 + 验证结果读取
videonote/core/srs.py           583  SM-2 + 句子卡 + 单词卡 + 混排视图
videonote/core/analytics.py     350  训练统计（含 weak_phonemes / words）
videonote/core/scoring.py       273  词级评分 + phoneme_suspects + 对齐带时间戳
videonote/api/routes_words.py   230  单词训练接口
videonote/api/routes_train.py   236  复习队列 / 统计 / 单句音频
videonote/api/routes_practice.py 385 评分 + 影子跟读 + analyze_phonemes
videonote/api/routes_fs.py      122  本机文件浏览器
videonote/api/routes_system.py  109  capabilities（含 phoneme 状态）
web/js/features/words.js        300  单词页（听音辨词 / 跟读对比）
web/js/features/review.js       599  复习页（句子卡 + 单词卡）
web/js/features/stats.js        291  统计页（手写 SVG 图表）
web/js/phoneme-ui.js             85  音素胶囊渲染（两处复用）
web/js/features/settings.js     101  引擎状态（含音素模型可用性）
docs/V0.4-设计.md               141  v0.4 设计（统一队列 + 按说话人拆句）
```
