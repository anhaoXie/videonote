# -*- coding: utf-8 -*-
"""pipeline 包：里面每个模块都是一个可插拔的处理阶段（Stage）。

加新功能的标准流程：
    1. 在这个目录新建 xxx.py
    2. 写一个 class XxxStage(Stage) 并加 @register_stage
    3. 在 pipeline/jobkinds.py 里把它串进某个任务流程（或注册新流程）
前端不用改一行代码——它会自动出现在「任务类型」下拉框里。
"""
from . import jobkinds  # noqa: F401  （注册任务类型，必须最后导入）
