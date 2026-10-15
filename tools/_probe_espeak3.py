# -*- coding: utf-8 -*-
"""验证：用 Windows 8.3 短路径（纯 ASCII）喂给 espeak-ng 能不能绕开中文路径问题。"""
import ctypes

import espeakng_loader as E

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
k32.GetShortPathNameW.restype = ctypes.c_uint


def short_path(p: str) -> str:
    buf = ctypes.create_unicode_buffer(600)
    n = k32.GetShortPathNameW(p, buf, 600)
    return buf.value if n else p


data = str(E.get_data_path())
sp = short_path(data)
print("长路径:", data)
print("短路径:", sp)
print("短路径纯 ASCII:", sp.isascii())

lib = ctypes.CDLL(str(E.get_library_path()))
lib.espeak_Initialize.restype = ctypes.c_int
lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.espeak_SetVoiceByName.restype = ctypes.c_int
lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
lib.espeak_TextToPhonemes.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

rc = lib.espeak_Initialize(0, 0, sp.encode("ascii"), 0)
print("espeak_Initialize(短路径) ->", rc)
if rc > 0:
    print("  SetVoice en-us ->", lib.espeak_SetVoiceByName(b"en-us"))
    for word in (b"think", b"very", b"right", b"pronunciation", b"the", b"cat"):
        buf = ctypes.c_char_p(word)
        out = []
        for _ in range(6):
            r = lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 2)
            if not r:
                break
            out.append(r.decode("utf-8", "ignore"))
        print(f"  {word.decode():16} -> {' '.join(out)!r}")
