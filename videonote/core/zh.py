# -*- coding: utf-8 -*-
"""繁简规范化：把大模型偶尔吐出的繁体字统一成简体。

为什么需要这一层：
    prompt 里写「输出简体中文」只是**软约束**。DeepSeek 等模型在长文本、
    文言腔或港台语境下仍会输出繁体（這 / 個 / 們 / 說 / 時 / 間 …）。
    字幕是给外语学习者看的，混着繁体直接影响可读性，而且用户明确要求
    「只有简体中文和英文两种文字」。所以这里做一层**确定性**兜底：
    不依赖模型听话，转换不出错。

    转换器缺失时**不报错、原样返回**——宁可显示繁体，也不让整条流水线挂掉。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_converter = None
_tried = False


def _get():
    """惰性初始化 OpenCC（首次 import 约几十毫秒，不要放在模块顶层）。"""
    global _converter, _tried
    if _tried:
        return _converter
    _tried = True
    try:
        from opencc import OpenCC  # type: ignore

        _converter = OpenCC("t2s")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "opencc 不可用（%s），繁体字将不会被自动转成简体。"
            "安装：pip install opencc-python-reimplemented",
            exc,
        )
        _converter = None
    return _converter


def available() -> bool:
    return _get() is not None


def to_simplified(text: str) -> str:
    """繁体 → 简体。转换器不可用或转换异常时原样返回，绝不抛异常。"""
    if not text:
        return text
    cc = _get()
    if cc is None:
        return text
    try:
        return cc.convert(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("繁简转换失败，原样返回：%s", exc)
        return text


def has_traditional(text: str) -> bool:
    """粗判一段文本里有没有繁体（转换后会变就认为有）。用于日志与自检。"""
    if not text:
        return False
    return to_simplified(text) != text
