# -*- coding: utf-8 -*-
"""FastAPI 应用装配：注册核心路由 + 插件路由 + 静态前端。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, db
from ..config import settings
from ..core.jobs import worker
from ..core.registry import registry
from . import routes_jobs, routes_media, routes_projects, routes_system


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()
    db.init()
    stale = db.recover_orphan_jobs()
    if stale:
        print(f"[videonote] 清理了 {stale} 个上次中断的任务")
    orphans = db.cleanup_orphans()
    if orphans["clips"] or orphans["scores"]:
        print(f"[videonote] 清理孤儿数据：句库 {orphans['clips']} 条 / 评分 {orphans['scores']} 条")
    registry.autodiscover()
    worker.start()
    print(f"[videonote] 已注册 {len(registry.stages)} 个处理阶段 / {len(registry.job_kinds)} 种任务类型")
    if getattr(registry, "import_errors", None):
        for mod, err in registry.import_errors.items():
            print(f"[videonote] 插件加载失败: {mod} -> {err}")
    yield
    worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="VideoNote 视频学习工作台", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1", "http://localhost"],
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = APIRouter(prefix="/api")
    api.include_router(routes_system.router)
    api.include_router(routes_projects.router)
    api.include_router(routes_jobs.router)
    api.include_router(routes_media.router)
    app.include_router(api)

    # 前端静态资源一律不缓存：这是本机工具，性能无所谓，
    # 但「浏览器拿着旧 JS → 页面点不动」这个坑必须彻底堵死。
    @app.middleware("http")
    async def _no_store_static(request, call_next):  # noqa: ANN001
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response

    # 插件注册的 router 也挂在 /api 下
    registry.autodiscover()
    for r in registry.routers:
        app.include_router(r, prefix="/api")

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # noqa: ANN001
        return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})

    web_dir = settings.web_dir
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app


app = create_app()
