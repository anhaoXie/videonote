# -*- coding: utf-8 -*-
"""验证 espeak-ng 的中文路径问题：UTF-8 vs ANSI(mbcs) 编码传数据目录。"""
import ctypes

import espeakng_loader as E

lib = ctypes.CDLL(str(E.get_library_path()))
lib.espeak_Initialize.restype = ctypes.c_int
lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.espeak_SetVoiceByName.restype = ctypes.c_int
lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
lib.espeak_TextToPhonemes.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

data = str(E.get_data_path())
print("数据目录:", data)
print("mbcs 可编码:", end=" ")
try:
    enc = data.encode("mbcs")
    print("是 ->", enc[:40], "...")
except Exception as exc:  # noqa: BLE001
    print("否 ->", exc)
    enc = data.encode("utf-8")

rc = lib.espeak_Initialize(0, 0, enc, 0)
print("espeak_Initialize(mbcs) ->", rc)
if rc > 0:
    print("  SetVoice en-us ->", lib.espeak_SetVoiceByName(b"en-us"))
    for word in (b"think", b"very", b"right", b"pronunciation", b"the"):
        buf = ctypes.c_char_p(word)
        out = []
        for _ in range(6):
            r = lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 2)
            if not r:
                break
            out.append(r.decode("utf-8", "ignore"))
        print(f"  {word.decode():16} -> {' '.join(out)!r}")
else:
    print("  失败，返回码 <= 0")
