# -*- coding: utf-8 -*-
"""临时自检：音素评测依赖装好了没。"""
import importlib
import sys

for name in ("torch", "transformers", "tokenizers", "phonemizer", "espeakng_loader", "safetensors"):
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "ok")
        print(f"  {name:16} {ver}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name:16} 缺失 ({type(exc).__name__}: {exc})")
print("python", sys.version.split()[0])
