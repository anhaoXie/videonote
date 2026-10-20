"""后台任务工人：单线程顺序消费队列（ASR 吃满 CPU，并发没有意义）。"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Optional

from .. import db
from ..models import JobStatus, now_iso
from . import events
from .stages import PipelineRunner, StageCanceled


class Worker:
    def __init__(self, runner: Optional[PipelineRunner] = None, poll_interval: float = 1.0) -> None:
        if runner is None:
            from .stages import runner as default_runner

            runner = default_runner
        self.runner = runner
        self.poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._busy = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="vn-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            job = db.claim_next_job()
            if not job:
                time.sleep(self.poll_interval)
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        self._busy.set()
        jid = job["id"]
        pid = job.get("project_id")
        try:
            if db.is_canceled(jid):
                raise StageCanceled()
            if str(job.get("kind") or "").startswith("course"):
                # 课程生成不隶属于项目，走独立执行器；
                # 排队、取消、进度、日志仍复用同一套机制。
                from . import course_jobs

                result = course_jobs.run(job)
            else:
                result = self.runner.run_job(job)
            db.update_job(jid, status=JobStatus.DONE, progress=100, message="完成",
                          result=result, finished_at=now_iso())
            if pid:
                db.update_project(pid, status="ready")
            events.notify(jid, pid, JobStatus.DONE, "完成")
        except StageCanceled:
            db.update_job(jid, status=JobStatus.CANCELED, message="已取消", finished_at=now_iso())
            events.notify(jid, pid, JobStatus.CANCELED, "已取消")
        except Exception as exc:  # noqa: BLE001
            detail = traceback.format_exc(limit=8)
            db.add_log(jid, f"任务失败: {exc}\n{detail}", "error")
            db.update_job(jid, status=JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}",
                          message="失败", finished_at=now_iso())
            if pid:
                db.update_project(pid, status="failed")
            events.notify(jid, pid, JobStatus.FAILED, str(exc))
        finally:
            self._busy.clear()


worker = Worker()
