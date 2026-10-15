"""插件注册中心 —— 这是「可塑性」的核心。

任何新功能都只需要三件事：
  1. 写一个 Stage 子类（处理阶段）
  2. 用 @register_stage 贴上标签
  3. 放进 videonote/pipeline/ 目录（或用 entry point 从外部包导入）

也可以注册：
  * Provider      —— 换成别的 ASR / 大模型 / 视觉模型
  * API Router    —— 加自己的 HTTP 接口
  * UI Module     —— 前端加一个标签页（web/js/features/*.js 里调 VN.ui.registerTab）
  * JobKind       —— 一种新的任务流程（把若干 Stage 串起来）
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Type

__all__ = [
    "StageRegistry",
    "register_stage",
    "register_provider",
    "register_router",
    "register_job_kind",
    "registry",
]


class Registry:
    def __init__(self) -> None:
        self.stages: Dict[str, type] = {}
        self.providers: Dict[str, Dict[str, Any]] = {"asr": {}, "llm": {}, "vision": {}}
        self.routers: List[Any] = []
        self.job_kinds: Dict[str, dict] = {}
        self._loaded = False

    # ---------------- Stage ----------------
    def add_stage(self, cls: type) -> type:
        name = getattr(cls, "name", None)
        if not name:
            raise ValueError(f"Stage {cls!r} 缺少 name 属性")
        self.stages[name] = cls
        return cls

    def stage(self, name: str) -> Optional[type]:
        return self.stages.get(name)

    def stage_instances(self) -> List[Any]:
        return [cls() for cls in self.stages.values()]

    # ---------------- Provider ----------------
    def add_provider(self, kind: str, name: str, factory: Callable[..., Any]) -> None:
        self.providers.setdefault(kind, {})[name] = factory

    def get_provider(self, kind: str, name: str, **kwargs: Any) -> Any:
        table = self.providers.get(kind, {})
        if name not in table:
            raise KeyError(f"未注册的 {kind} provider: {name}（可选：{list(table)}）")
        return table[name](**kwargs)

    def has_provider(self, kind: str, name: str) -> bool:
        return name in self.providers.get(kind, {})

    # ---------------- Router ----------------
    def add_router(self, router: Any) -> Any:
        self.routers.append(router)
        return router

    # ---------------- Job kind ----------------
    def add_job_kind(self, name: str, stages: List[str], description: str = "", label: str = "") -> None:
        self.job_kinds[name] = {"stages": list(stages), "description": description, "label": label or name}

    # ---------------- 自动发现 ----------------
    def autodiscover(self) -> None:
        """导入 videonote/pipeline 和 videonote/providers 下的所有模块，触发装饰器注册。

        单个模块导入失败（例如可选依赖没装）不会影响其它插件，只记录下来。
        """
        if self._loaded:
            return
        import importlib
        import pkgutil
        import traceback

        from .. import api as api_pkg
        from .. import pipeline as pipeline_pkg
        from .. import providers as providers_pkg

        self.import_errors: Dict[str, str] = {}
        for pkg in (providers_pkg, pipeline_pkg):
            for mod in pkgutil.iter_modules(pkg.__path__):
                if mod.name.startswith("_"):
                    continue
                full = f"{pkg.__name__}.{mod.name}"
                try:
                    importlib.import_module(full)
                except Exception as exc:  # noqa: BLE001
                    self.import_errors[full] = f"{type(exc).__name__}: {exc}"
                    print(f"[registry] 插件 {full} 加载失败: {exc}\n{traceback.format_exc(limit=2)}")
        # 插件路由：videonote/api/routes_*.py（用 @register_router 注册）
        for mod in pkgutil.iter_modules(api_pkg.__path__):
            if not mod.name.startswith("routes_"):
                continue
            full = f"{api_pkg.__name__}.{mod.name}"
            try:
                importlib.import_module(full)
            except Exception as exc:  # noqa: BLE001
                self.import_errors[full] = f"{type(exc).__name__}: {exc}"
                print(f"[registry] 路由 {full} 加载失败: {exc}\n{traceback.format_exc(limit=2)}")
        self._loaded = True


registry = Registry()


# --------------------------------------------------------------------------
# 装饰器
# --------------------------------------------------------------------------
def register_stage(cls: type) -> type:
    return registry.add_stage(cls)


def register_provider(kind: str, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        registry.add_provider(kind, name, fn)
        return fn

    return deco


def register_router(router: Any) -> Any:
    return registry.add_router(router)


def register_job_kind(name: str, stages: List[str], description: str = "", label: str = "") -> None:
    registry.add_job_kind(name, stages, description, label)


StageRegistry = Registry
