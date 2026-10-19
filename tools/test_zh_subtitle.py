# -*- coding: utf-8 -*-
"""繁简兜底的回归测试（离线，不调用任何 API）。

为什么要有这个测试：
    「字幕是繁体」这件事的根因是——prompt 里写了简体，但那只是软约束。
    所以修复分两层：prompt 加强 + **确定性转换**。这个测试专门锁住第二层：
    即使模型返回繁体，落库的译文也必须是简体。

跑法：
    .\\.venv\\Scripts\\python.exe tools\\test_zh_subtitle.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videonote.core.zh import available, has_traditional, to_simplified  # noqa: E402
from videonote.pipeline import translate as T  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


# ---------------------------------------------------------------------------
# 1. 转换器本身
# ---------------------------------------------------------------------------
print("\n[1] opencc 转换器")
check("opencc 可用", available())
cases = [
    ("這是一部戰爭片，我們認為這樣很好。", "这是一部战争片，我们认为这样很好。"),
    ("這個問題很難，時間不夠。", "这个问题很难，时间不够。"),
    ("他说：没问题。", "他说：没问题。"),          # 本来就是简体，不能被改坏
    ("HELLO WORLD", "HELLO WORLD"),                # 英文原样
    ("", ""),
]
for src, want in cases:
    got = to_simplified(src)
    check(f"{src[:12]!r} -> 简体", got == want, f"得到 {got!r}，期望 {want!r}")

check("繁体检测：這個", has_traditional("這個"))
check("繁体检测：这个 不算", not has_traditional("这个"))

# ---------------------------------------------------------------------------
# 2. 翻译阶段：模型返回繁体时，落库必须是简体
# ---------------------------------------------------------------------------
print("\n[2] translate 阶段（模型故意返回繁体）")

TRAD = {
    "0": "這是一個很好的問題。",
    "1": "我覺得時間不夠。",
    "2": "他們說沒問題。",
}


class FakeProvider:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt, system=None, retries=2):
        self.calls += 1
        return dict(TRAD)


class FakeCtx:
    params: dict = {}
    project_id = "p_test"

    def __init__(self) -> None:
        self.saved = {}
        self.logs = []

    def segments(self):
        return [
            {"idx": 0, "text": "That's a good question.", "start": 0.0, "end": 2.0},
            {"idx": 1, "text": "I don't think we have time.", "start": 2.0, "end": 4.0},
            {"idx": 2, "text": "They said it's fine.", "start": 4.0, "end": 6.0},
        ]

    def check_cancel(self):
        return None

    def set_progress(self, pct, msg=""):
        return None

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def save_json(self, key, obj, filename, meta=None):
        self.saved[key] = obj


ctx = FakeCtx()
fake = FakeProvider()
T.get_llm_provider = lambda *a, **k: fake          # 替换掉真实 provider
out = T.TranslateStage().run(ctx)

saved = ctx.saved.get("translation", {})
check("译文已落库", bool(saved), f"saved={ctx.saved}")
for idx, val in saved.items():
    check(f"第 {idx} 句是简体", not has_traditional(val), f"{val!r} 仍含繁体")
check("3 句全转成功", len(saved) == 3, f"只有 {len(saved)} 句")

warned = any("繁体" in m for _, m in ctx.logs)
check("日志里记了「含繁体已转简体」", warned, f"logs={ctx.logs}")

# ---------------------------------------------------------------------------
print(f"\n结果：{PASS} 通过，{FAIL} 失败")
sys.exit(1 if FAIL else 0)
