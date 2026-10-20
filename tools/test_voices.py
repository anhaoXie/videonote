r"""检查本机的离线语音是否可用（课程视频配音用）。

用法：
    .\.venv\Scripts\python.exe tools\test_voices.py

背景：配音链路是 Windows 自带语音（SAPI5）优先、espeak-ng 兜底。
SAPI 依赖系统里注册的语音令牌，在**受限进程**（沙箱、服务、部分远程会话）里
会报「没有安装该语音」或 COM 错误 0x80045040 —— 那不是代码问题。
如果这里失败，请在普通终端（或桌面图标启动的进程）里再跑一次。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videonote.core import tts  # noqa: E402


def main() -> int:
    print("== 1) comtypes ==")
    try:
        import comtypes  # noqa: F401

        print("   ✅ 已安装")
    except Exception as exc:  # noqa: BLE001
        print(f"   ❌ 未安装：{exc}   修复：pip install comtypes")

    print("== 2) SAPI 类型库 ==")
    print("   ✅ 已加载" if tts._ensure_speechlib() else "   ❌ 加载失败")

    print("== 3) 已安装语音 ==")
    voices = tts.sapi_voices()
    for v in voices or ["（取不到，可能被进程权限限制）"]:
        print(f"   {v}")

    print("== 4) 合成测试 ==")
    out = Path("data/_tmp/voice_test.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    p = tts.synth_windows("这是配音测试，用来确认本机语音是否可用。", out, wpm=150)
    if p is None:
        print("   ❌ Windows 语音不可用（会自动退回 espeak）")
        print("      若你是在受限环境里跑的，请在普通终端再试一次。")
    else:
        try:
            import soundfile as sf

            d, sr = sf.read(str(p))
            print(f"   ✅ 成功：{out}   {len(d) / sr:.2f}s   {sr}Hz")
        except Exception:  # noqa: BLE001
            print(f"   ✅ 成功：{out}（{out.stat().st_size} 字节）")

    print("== 5) espeak 兜底 ==")
    a = tts.synth("This is a fallback test.", wpm=150)
    print(f"   {'✅ 可用' if a is not None else '❌ 不可用'}")

    return 0 if p is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
