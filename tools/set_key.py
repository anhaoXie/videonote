# -*- coding: utf-8 -*-
"""把 API key 写进 .env 并当场验证能不能用。

    .venv\\Scripts\\python.exe tools\\set_key.py llm sk-xxxx
    .venv\\Scripts\\python.exe tools\\set_key.py asr sk-xxxx
    .venv\\Scripts\\python.exe tools\\set_key.py vlm sk-xxxx
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

TARGETS = {
    "llm": ("VN_LLM_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat"),
    "asr": ("VN_ASR_API_KEY", "https://api.siliconflow.cn/v1", None),
    "vlm": ("VN_VLM_API_KEY", "https://api.siliconflow.cn/v1", None),
}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    which, key = sys.argv[1].lower(), sys.argv[2].strip()
    if which not in TARGETS:
        print(f"未知目标 {which}，可选：{list(TARGETS)}")
        return 1
    var, base_url, model = TARGETS[which]
    key = key.strip().strip('"').strip("'")

    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    if re.search(rf"(?m)^{var}=.*$", text):
        text = re.sub(rf"(?m)^{var}=.*$", f"{var}={key}", text)
    else:
        text = text.rstrip() + f"\n{var}={key}\n"
    ENV.write_text(text, encoding="utf-8")
    print(f"✅ 已写入 {var}（长度 {len(key)}）")

    if which == "llm":
        print("\n正在验证大模型 key…")
        try:
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "只回复两个字：可用"}],
                      "max_tokens": 20},
                timeout=60,
            )
            if r.status_code == 200:
                print("模型回复:", r.json()["choices"][0]["message"]["content"].strip())
                print("✅ key 可用，摘要笔记现在是高质量模式")
            else:
                print(f"❌ 返回 {r.status_code}: {r.text[:300]}")
        except Exception as exc:  # noqa: BLE001
            print("❌ 请求失败:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
