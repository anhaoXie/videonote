# 加新功能的三步（插件开发指南）

VideoNote 的设计目标就是「**加功能不用改既有代码**」。下面每个例子都是可以直接抄的最小实现。

---

## 0. 先理解两个约定

1. **Stage（处理阶段）之间只通过 artifact 通信**：`ctx.save_json("key", obj)` / `ctx.load_json("key")`。
2. **注册即生效**：`videonote/pipeline/` 和 `videonote/providers/` 下的模块会被自动导入，
   装饰器一执行就注册好了；前端下拉框读的是后端的能力清单，不用改前端。

`StageContext` 给你的东西：

| 成员 | 说明 |
| --- | --- |
| `ctx.project` / `ctx.project_id` | 当前项目 |
| `ctx.params` | 任务参数（前端/接口传进来的，比如 `{"language": "en"}`） |
| `ctx.media_path` / `ctx.audio_path()` | 媒体路径 / 音频路径 |
| `ctx.segments()` | 逐句列表（查 SQLite） |
| `ctx.transcript()` | 完整 Transcript 对象 |
| `ctx.save_json(key, obj)` / `ctx.load_json(key)` / `ctx.artifact_path(key, name)` | 产出物读写 |
| `ctx.set_progress(pct, msg)` / `ctx.log(msg)` / `ctx.check_cancel()` | 进度、日志、取消响应 |

---

## 例 1：加一个处理阶段 —— 「导出 Anki 跟读卡」

> 这个例子已经实现了：见 `videonote/api/routes_clips.py`（真实的导出是用 genanki 打成
> `.apkg`，比 CSV 更好用）。这里保留它作为「最小插件」的教学样例。

新建 `videonote/pipeline/anki.py`：

```python
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext


@register_stage
class AnkiStage(Stage):
    name = "anki"                       # 唯一名字，jobkinds 里用它
    label = "导出 Anki 卡片"
    description = "把转写句子做成带音频的 Anki 记忆卡"
    requires = ("transcript",)          # 缺这个产出就跳过
    produces = "anki_csv"               # 主产出（决定幂等跳过）
    optional = True                     # 失败不中断整条管线

    def run(self, ctx: StageContext) -> dict:
        segs = ctx.segments()
        if not segs:
            raise RuntimeError("没有句子")
        rows = ["#separator:Tab", "#html:true", "#tags:videonote"]
        for s in segs:
            audio_tag = f"[sound:{ctx.project_id}_{s['idx']}.mp3]"   # 需要配套切片
            rows.append(f"{s['text']}\t{audio_tag}\t{s['start']:.2f}")
            ctx.set_progress(s["idx"] / len(segs) * 100, f"生成卡片 {s['idx']+1}/{len(segs)}")
        out = ctx.artifact_path("anki_csv", "anki_cards.txt")
        out.write_text("\n".join(rows), encoding="utf-8")
        ctx.save_artifact("anki_csv", out.name, kind="text")
        return {"cards": len(segs)}
```

然后打开 `videonote/pipeline/jobkinds.py` 加一行：

```python
register_job_kind("anki", ["anki"], description="从转写导出 Anki 卡片", label="导出 Anki 卡片")
```

**重启服务**：任务类型下拉框里立刻多出「导出 Anki 卡片」。前端一行没改。

---

## 例 2：加一个界面标签页

新建 `web/js/features/anki.js`：

```javascript
import { VN, api, store, esc } from '../core.js';

VN.ui.registerTab({
  id: 'anki', label: 'Anki 卡片', order: 50,
  async render(panel) {
    if (!store.project) { panel.innerHTML = '<div class="card">先选项目</div>'; return; }
    panel.innerHTML = '<div class="card"><h3>Anki 卡片</h3><div id="a-body">加载中…</div></div>';
    // 想加自己的接口？后端注册一个 router 就行（见例 3），这里直接 fetch
    const res = await fetch(`/api/projects/${store.project.id}/artifact/anki_csv`);
    document.getElementById('a-body').innerHTML = res.ok
      ? `<a class="btn" href="${api.artifactUrl(store.project.id, 'anki_csv', true)}">下载卡片文件</a>`
      : '<span class="muted">还没有卡片，先跑「导出 Anki 卡片」任务</span>';
  },
});
```

在 `web/app.js` 里 import 一行：`import './js/features/anki.js';` —— 导航栏就多一个标签页。

---

## 例 3：加自己的 HTTP 接口

在 `videonote/api/` 下新建 **`routes_xxx.py`**（文件名必须以 `routes_` 开头，注册中心会自动导入）：

```python
# videonote/api/routes_anki.py
from fastapi import APIRouter
from ..core.registry import register_router

router = APIRouter(tags=["anki"])

@router.get("/anki/hello")
def hello():
    return {"msg": "挂在 /api/anki/hello"}

register_router(router)      # 这一行必须有
```

`videonote/api/app.py` 会把注册进来的 router 自动挂到 `/api` 下，不用改任何既有文件。
（注意路由冲突：路径别和已有的重复，FastAPI 是按注册顺序匹配的。）

---

## 例 4：换一个能力实现（Provider）

想用视觉大模型描述关键帧？注册一个新的 vision provider 即可，业务代码不用动：

```python
# videonote/providers/vision_qwen.py
from ..core.registry import register_provider
from .vision_opencv import VLMVision

class QwenVL(VLMVision):
    name = "qwen_vl"
    def __init__(self):
        super().__init__(base_url="https://api.siliconflow.cn/v1",
                         api_key=settings.asr_api_key,
                         model="Qwen/Qwen2.5-VL-72B-Instruct")

@register_provider("vision", "qwen_vl")
def _make():
    return QwenVL()
```

然后任务参数里传 `{"vision_provider": "qwen_vl"}`（或把 `frames` 阶段的默认值改成它）。
同样方式可以加：`asr`（讯飞/本地 whisper.cpp server）、`llm`（Ollama / vLLM / 通义）。

---

## 例 5：让阶段产出「可复用」而不是每次重跑

- `produces` 有值且 artifact 已存在 → 自动跳过（除非任务参数里 `force: ["anki_csv"]` 或 `force_all: true`）。
- 想让用户能单独重跑，就走 `POST /api/projects/{pid}/jobs`，body 里带 `params.force`。
- 前端「重做摘要笔记」按钮就是用这个机制（`kind=summarize` → 只有 `notes` 阶段，且默认跳过，需要 force）。

---

## 注意事项（踩过的坑）

1. **别在 Stage 里直接开多个进程/线程**：Worker 是单线程的（见 `docs/ARCHITECTURE.md` 第 6 节）。
2. **耗时的循环里加 `ctx.check_cancel()`**，否则用户取消失效。
3. **可选依赖要懒加载**（放进函数内部 import），否则插件导入失败会连累启动 —— 注册中心虽然容错，但会记录到 `/api/capabilities` 的 `plugins.import_errors` 里。
4. **artifact key 用蛇形命名**，前端 `library.js` 的 `ART_LABEL` 表里可以补中文名。
5. **改了 `SCHEMA`** 记得删掉 `data/videonote.db` 重建，或者自己写 `ALTER TABLE`（当前没有迁移框架）。
6. **模型/大文件放 `data/` 下**，已经在 `.gitignore` 里。
7. **Windows + 中文路径**：`cv2.imwrite` 会静默失败（返回 False），要写成
   `cv2.imencode` + `Path.write_bytes`；创建符号链接会 `WinError 1314`（参考
   `providers/diarize_local.py` 里给 speechbrain 打的补丁）。
8. **HF 缓存**：已经统一到 `data/hf`（`HF_HOME`），别写用户主目录 —— 有些机器上会被拒绝访问。
