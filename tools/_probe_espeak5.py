# -*- coding: utf-8 -*-
"""验证：给 espeak 传「相对路径」能不能绕开中文路径（此时进程 CWD 也在中文目录下）。"""
import ctypes
import os
from pathlib import Path

import espeakng_loader as E

lib = ctypes.CDLL(str(E.get_library_path()))
lib.espeak_Initialize.restype = ctypes.c_int
lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.espeak_SetVoiceByName.restype = ctypes.c_int
lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
lib.espeak_TextToPhonemes.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

pkg = Path(E.__file__).parent
print("切到:", pkg)
os.chdir(pkg)

for arg in (b"espeak-ng-data", b".\\espeak-ng-data", b"espeak-ng-data\\"):
    print(f"\n--- 尝试相对路径 {arg!r} ---")
    rc = lib.espeak_Initialize(0, 0, arg, 0)
    print("  初始化返回:", rc)
    if rc > 0:
        lib.espeak_SetVoiceByName(b"en-us")
        buf = ctypes.c_char_p(b"think")
        out = []
        for _ in range(6):
            r = lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 2)
            if not r:
                break
            out.append(r.decode("utf-8", "ignore"))
        print("  think ->", out)
        break
