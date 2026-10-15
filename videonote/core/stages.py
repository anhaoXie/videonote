"""管线执行框架：Stage 基类 + 上下文 + 顺序执行器。

数据流全是 artifact：
    ingest -> media
    audio  -> audio
    asr    -> transcript
    frames -> frames(关键帧) + scene_index
    notes  -> notes(笔记 markdown) + summary(json)
    subtitles -> srt / vtt (+ 可选双语、压制)

每个 Stage 都能单独重跑（force=True）；resume 时已存在的 artifact 会自动跳过。
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .. import db
from ..config import settings
from ..models import JobStatus, Transcript, dumps, new_id, now_iso
from . import events
from .registry import registry


class StageCanceled(Exception):
    """用户取消任务时抛出。"""


# --------------------------------------------------------------------------
class StageContext:
    """Stage 运行时拿到的一切。"""

    def __init__(self, job: dict, project: dict, params: Optional[dict] = None, force: bool = False) -> None:
        self.job = job
        self.job_id: str = job["id"]
        self.project = project
        self.project_id: str = project["id"]
        self.params: dict = params or {}
        self.force: bool = force
        self.dir: Path = settings.project_dir(self.project_id)
        self._stage = ""
        self._stage_pct = 0.0
        self._stage_index = 0
        self._stage_total = 1
        self._last_pct = -1.0
        self._last_emit = 0.0
        self._last_log = 0.0
        for sub in ("artifacts", "audio", "frames", "notes", "subtitles", "media", "logs"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

    # ---------------- 进度 ----------------
    def begin_stage(self, stage: str, index: int, total: int) -> None:
        self._stage, self._stage_index, self._stage_total = stage, index, total
        self._stage_pct = 0.0

    @property
    def overall_pct(self) -> float:
        if self._stage_total <= 0:
            return 0.0
        return (self._stage_index + max(0.0, min(1.0, self._stage_pct))) / self._stage_total * 100.0

    def set_progress(self, pct: float, message: str = "") -> None:
        """pct: 当前 Stage 内部进度 0-100。

        对高频调用做了节流：变化小于 0.5% 且距上次不到 0.7 秒就跳过，
        避免长视频识别时把上千条日志灌进数据库。
        """
        pct = max(0.0, min(100.0, float(pct)))
        now = time.time()
        if pct < 100.0 and (pct - self._last_pct < 0.5) and (now - self._last_emit < 0.7):
            return
        self._last_pct, self._last_emit = pct, now
        self._stage_pct = pct / 100.0
        events.progress(self.job_id, self.project_id, self._stage, self.overall_pct, message)
        db.update_job(self.job_id, progress=round(self.overall_pct, 2), message=message or self._stage)
        if message and (now - self._last_log > 1.5 or pct >= 100.0):
            self._last_log = now
            db.add_log(self.job_id, f"[{self._stage}] {message}")

    def log(self, message: str, level: str = "info") -> None:
        db.add_log(self.job_id, f"[{self._stage}] {message}", level)
        events.log_line(self.job_id, self.project_id, message, level)

    def check_cancel(self) -> None:
        if db.is_canceled(self.job_id):
            raise StageCanceled()

    # ---------------- artifact ----------------
    def has_artifact(self, key: str) -> bool:
        return db.get_artifact(self.project_id, key) is not None

    def get_artifact(self, key: str) -> Optional[dict]:
        return db.get_artifact(self.project_id, key)

    def artifact_path(self, key: str, filename: str) -> Path:
        """生成 artifact 文件路径（按 key 分目录）。"""
        d = self.dir / "artifacts" / key
        d.mkdir(parents=True, exist_ok=True)
        return d / filename

    def put_artifact(self, key: str, path: str | Path, kind: str = "file", meta: Optional[dict] = None) -> Path:
        """把一个「已经存在的文件」登记成 artifact（媒体文件在原位时用这个）。"""
        p = Path(path)
        db.put_artifact(self.project_id, key, str(p), kind=kind, meta=meta or {})
        return p

    def save_artifact(self, key: str, filename: str, kind: str = "file", meta: Optional[dict] = None) -> Path:
        p = self.artifact_path(key, filename)
        db.put_artifact(self.project_id, key, str(p), kind=kind, meta=meta)
        return p

    def save_json(self, key: str, obj: Any, filename: str = "data.json", meta: Optional[dict] = None) -> Path:
        p = self.artifact_path(key, filename)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        db.put_artifact(self.project_id, key, str(p), kind="json", meta=meta)
        return p

    def load_json(self, key: str, default: Any = None) -> Any:
        art = db.get_artifact(self.project_id, key)
        if not art or not art.get("path"):
            return default
        p = Path(art["path"])
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def artifact_file(self, key: str) -> Optional[Path]:
        art = db.get_artifact(self.project_id, key)
        if not art or not art.get("path"):
            return None
        p = Path(art["path"])
        return p if p.exists() else None

    # ---------------- 数据快捷访问 ----------------
    @property
    def media_path(self) -> Optional[str]:
        """媒体文件路径：优先项目表里的 media_path（ingest 写入），其次 media_file 产出。"""
        p = self.project.get("media_path") or None
        if p and Path(p).exists():
            return p
        art = db.get_artifact(self.project_id, "media_file")
        if art and art.get("path"):
            return art["path"]
        return p

    def audio_path(self) -> Optional[Path]:
        return self.artifact_file("audio")

    def transcript(self) -> Optional[Transcript]:
        data = self.load_json("transcript")
        return Transcript.from_dict(data) if data else None

    def segments(self) -> List[dict]:
        return db.list_segments(self.project_id)

    def frames(self) -> List[dict]:
        return self.load_json("frames", []) or []


# --------------------------------------------------------------------------
class Stage:
    """一个处理阶段。子类必须实现 run()。"""

    name: str = "stage"
    label: str = ""
    description: str = ""
    requires: tuple = ()      # 依赖的 artifact key，缺失则跳过并报错
    produces: str = ""        # 主产出 artifact key（用于跳过判断 & 前端展示）
    optional: bool = False    # True 时失败不中断整个管线

    def should_skip(self, ctx: StageContext) -> bool:
        return bool(self.produces) and not ctx.force and ctx.has_artifact(self.produces)

    def run(self, ctx: StageContext) -> Dict[str, Any]:  # pragma: no cover - 抽象
        raise NotImplementedError


# --------------------------------------------------------------------------
class PipelineRunner:
    def __init__(self) -> None:
        self.stages: Dict[str, Stage] = {}

    def resolve(self, names: List[str]) -> List[Stage]:
        out: List[Stage] = []
        for n in names:
            cls = registry.stage(n)
            if cls is None:
                raise KeyError(f"未注册的 Stage: {n}")
            out.append(cls())
        return out

    def run_job(self, job: dict) -> dict:
        project = db.get_project(job["project_id"]) if job.get("project_id") else None
        if project is None:
            raise ValueError(f"任务 {job['id']} 没有对应的 project")

        params = job.get("params") or {}
        kind = job["kind"]
        spec = registry.job_kinds.get(kind)
        if spec is None:
            raise KeyError(f"未知任务类型: {kind}（可用：{list(registry.job_kinds)}）")

        stage_names: List[str] = list(params.get("stages") or spec["stages"])
        force_keys: List[str] = list(params.get("force") or [])
        ctx = StageContext(job, project, params=params)
        stages = self.resolve(stage_names)
        total = len(stages)
        result: Dict[str, Any] = {"stages": []}

        events.notify(job["id"], project["id"], JobStatus.RUNNING, f"开始：{kind}")
        for i, stage in enumerate(stages):
            ctx.begin_stage(stage.name, i, total)
            ctx.force = stage.name in force_keys or stage.produces in force_keys or params.get("force_all", False)
            ctx.check_cancel()
            if stage.should_skip(ctx):
                ctx.log(f"跳过 {stage.name}（已有产出，无需重跑）")
                result["stages"].append({"stage": stage.name, "skipped": True})
                continue
            missing = [k for k in stage.requires if not ctx.has_artifact(k)]
            if missing:
                msg = f"{stage.name} 缺少前置产出 {missing}，已跳过"
                if stage.optional:
                    ctx.log(msg, "warn")
                    result["stages"].append({"stage": stage.name, "skipped": True, "reason": msg})
                    continue
                raise RuntimeError(msg)
            try:
                ctx.set_progress(0, f"开始 {stage.label or stage.name}")
                out = stage.run(ctx) or {}
                self._verify_artifacts(ctx, stage)
                result["stages"].append({"stage": stage.name, "ok": True, "output": out})
                db.update_project(project["id"], status="ready")
            except StageCanceled:
                raise
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc(limit=6)
                ctx.log(f"{stage.name} 失败: {exc}\n{detail}", "error")
                if stage.optional:
                    result["stages"].append({"stage": stage.name, "ok": False, "error": str(exc)})
                    continue
                raise
        return result

    @staticmethod
    def _verify_artifacts(ctx: StageContext, stage: Stage) -> None:
        """安全检查：Stage 声明的产出必须真实存在，否则早点报错（防止插件写错路径）。"""
        if not stage.produces:
            return
        art = db.get_artifact(ctx.project_id, stage.produces)
        if not art:
            return
        path = art.get("path") or ""
        if path and not Path(path).exists():
            raise RuntimeError(
                f"{stage.name} 声明的产出 {stage.produces} 指向不存在的文件：{path}"
                "（插件用了 save_artifact 但文件写到了别处，应该用 ctx.put_artifact 登记真实路径）"
            )


runner = PipelineRunner()
