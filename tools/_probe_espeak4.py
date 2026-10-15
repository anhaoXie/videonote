# -*- coding: utf-8 -*-
"""验证：把 espeak 的数据目录放到纯 ASCII 路径（junction 优先，失败则复制）后能否工作。"""
import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import espeakng_loader as E

real = Path(E.get_data_path())
base = Path(os.environ.get("VN_ESPEAK_DATA_DIR")
            or r"C:\Users\Public\VideoNote\espeak-ng-data")
print("真实数据目录:", real)
print("ASCII 目标  :", base)
print("目标已存在  :", base.exists())

if not base.exists():
    base.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(base), str(real)],
                        capture_output=True, text=True)
    print("mklink /J ->", rc.returncode, (rc.stdout or rc.stderr).strip()[:120])
    if rc.returncode != 0 or not base.exists():
        print("junction 失败，改为复制…")
        shutil.copytree(real, base, dirs_exist_ok=True)
        print("复制完成，大小:", sum(f.stat().st_size for f in base.rglob('*') if f.is_file()) // 1024, "KB")

if not base.exists():
    print("❌ 没能建立 ASCII 数据目录")
    raise SystemExit(1)

lib = ctypes.CDLL(str(E.get_library_path()))
lib.espeak_Initialize.restype = ctypes.c_int
lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.espeak_SetVoiceByName.restype = ctypes.c_int
lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
lib.espeak_TextToPhonemes.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

rc = lib.espeak_Initialize(0, 0, str(base).encode("ascii"), 0)
print("espeak_Initialize(ASCII) ->", rc)
if rc > 0:
    print("  SetVoice en-us ->", lib.espeak_SetVoiceByName(b"en-us"))
    for word in (b"think", b"very", b"right", b"pronunciation", b"the", b"cat", b"going"):
        buf = ctypes.c_char_p(word)
        out = []
        for _ in range(6):
            r = lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 2)
            if not r:
                break
            out.append(r.decode("utf-8", "ignore"))
        print(f"  {word.decode():16} -> {' '.join(out)!r}")
else:
    print("❌ 初始化失败")
