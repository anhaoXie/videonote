# -*- coding: utf-8 -*-
"""一键生成测试视频（人声 + 两段不同颜色的画面），用来验证整条管线。

    .venv\\Scripts\\python.exe tools\\make_test_video.py --sample

--sample 会直接下载一段公开的真实人声；不加参数则先尝试用 Windows 自带的
SAPI 语音合成中英文人声（部分机器上系统会拒绝写音频文件，那会自动回退到下载样本）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "test_video.mp4"
# 注意：这台机器上 tempfile.gettempdir() 会落到当前目录，所以统一用项目内的临时目录
TMP = ROOT / "data" / "_tmp"

LINES = [
    ("en", "Welcome to VideoNote. This is a test video for speech recognition."),
    ("en", "The quick brown fox jumps over the lazy dog."),
    ("en", "This tool can transcribe videos, summarize them, and create subtitles."),
    ("en", "It can also pause after every sentence so that you can repeat it aloud."),
    ("zh", "现在测试中文识别，这是一个视频学习工具，可以自动生成摘要和字幕。"),
    ("zh", "跟读模式会在每一句话结束后自动暂停，等你重复一遍。"),
]

PS_TEMPLATE = r"""
$ErrorActionPreference = 'Continue'
$lines = Get-Content -Raw -Encoding UTF8 '__JSON_PATH__' | ConvertFrom-Json
$out = '__WAV_PATH__'
$v = New-Object -ComObject SAPI.SpVoice
$all = $v.GetVoices()
$indexEn = 0; $indexZh = 0
for ($i = 0; $i -lt $all.Count; $i++) {
    $d = $all.Item($i).GetDescription()
    if ($d -match 'Zira|David|English') { $indexEn = $i }
    if ($d -match 'Huihui|Chinese|Yaoyao|Kangkang') { $indexZh = $i }
}
$fs = New-Object -ComObject SAPI.SpFileStream
$fs.Format.Type = 22          # 22kHz 16bit mono
$fs.Open($out, 3, $false)     # 3 = SSFMCreateForWrite
$v.AudioOutputStream = $fs
foreach ($line in $lines) {
    if ($line.lang -eq 'zh') { $v.Voice = $all.Item($indexZh) } else { $v.Voice = $all.Item($indexEn) }
    $v.Speak([string]$line.text) | Out-Null
    Start-Sleep -Milliseconds 250
}
$fs.Close()
Write-Host 'TTS_OK'
"""


def tts_wav(out: Path) -> None:
    """用 Windows SAPI（COM）合成中英文混读语音；失败抛异常，由上层回退到下载样本。"""
    TMP.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    jf = TMP / "vn_test_lines.json"
    jf.write_text(json.dumps([{"lang": l, "text": t} for l, t in LINES], ensure_ascii=False), encoding="utf-8")
    ps = PS_TEMPLATE.replace("__JSON_PATH__", str(jf)).replace("__WAV_PATH__", str(out))
    ps_file = TMP / "vn_tts.ps1"
    ps_file.write_text(ps, encoding="utf-8-sig")
    proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_file)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (proc.stderr or "").strip()[-400:]
    if not out.exists() or out.stat().st_size < 2000:
        raise RuntimeError(f"SAPI 语音合成失败（系统未允许写音频文件）。{tail}")


def download_sample(dst: Path) -> Path:
    """下载一段公开的真实人声（whisper.cpp 官方 11 秒英文演讲样本）。"""
    import urllib.request

    urls = [
        "https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav",
        "https://ghproxy.net/https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav",
        "https://raw.githubusercontent.com/ggerganov/whisper.cpp/master/samples/jfk.wav",
    ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    last = ""
    for u in urls:
        try:
            print(f"  尝试下载 {u}")
            with urllib.request.urlopen(u, timeout=30) as r, dst.open("wb") as f:
                f.write(r.read())
            if dst.stat().st_size > 10000:
                print(f"  下载成功: {dst.name} ({dst.stat().st_size} bytes)")
                return dst
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
    raise RuntimeError(f"下载测试音频失败（{last}）。请直接用自己的视频文件测试。")


def build_video(wav: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from videonote.core.media import probe, run_ffmpeg

    with wave.open(str(wav), "rb") as wf:
        dur = wf.getnframes() / wf.getframerate()
    dur = max(6.0, min(dur + 1.0, 180))
    half = round(dur / 2, 2)
    rest = round(dur - half, 2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 两段不同颜色的画面拼接 -> 制造一次场景切换，用来验证关键帧抽取
    args = [
        "-f", "lavfi", "-i", f"color=c=0x0b1b3a:s=640x360:d={half}:r=24",
        "-f", "lavfi", "-i", f"color=c=0x123a1b:s=640x360:d={rest}:r=24",
        "-i", str(wav),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", str(OUT),
    ]
    code, out = run_ffmpeg(args, timeout=600)
    if code != 0 or not OUT.exists():
        raise RuntimeError(f"ffmpeg 生成视频失败: {out[-800:]}")
    info = probe(OUT)
    print(f"生成完成: {OUT}  ({info.duration:.1f}s {info.width}x{info.height})")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成测试视频")
    ap.add_argument("--sample", action="store_true", help="直接用公开的人声样本（跳过 SAPI 合成）")
    args = ap.parse_args()

    if args.sample:
        print("1/2 获取公开人声样本…")
        tmp = download_sample(ROOT / "samples" / "sample_speech.wav")
    else:
        print("1/2 合成测试人声（中英混读）…")
        tmp = TMP / "vn_test_tts.wav"
        try:
            tts_wav(tmp)
            print("  SAPI 合成成功")
        except Exception as exc:  # noqa: BLE001
            print(f"  {exc}")
            print("  改用公开人声样本…")
            tmp = download_sample(ROOT / "samples" / "sample_speech.wav")

    print("2/2 合成测试视频…")
    build_video(tmp)
    print(f"\n在网页里用「本机路径」导入：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
