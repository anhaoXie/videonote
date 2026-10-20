# -*- coding: utf-8 -*-
"""准备数字人环境：Wav2Lip 代码 + 权重 + 两处兼容补丁。

用法（幂等，已存在的会跳过）：
    .\\.venv\\Scripts\\python.exe tools\\setup_avatar.py

为什么要这个脚本：
Wav2Lip 的代码和 500MB 权重**不进仓库**（data/ 是 gitignored），
但我们在真机上改过它两处，否则在本机根本跑不起来。这些改动必须有地方记下来，
否则换台机器/重装后数字人会神秘失效：

  补丁 1（audio.py）
    librosa 1.0 把 librosa.filters.mel 改成了纯关键字参数，
    Wav2Lip 还在用位置参数 -> TypeError: mel() takes 0 positional arguments

  补丁 2（inference.py）
    OpenCV 在 Windows 上读不了含中文的路径，cv2.imread 返回 None，
    于是人脸检测拿到空数组 -> TypeError: unsupported operand type(s) for -: 'NoneType' and 'int'
    -> imread 失败时用 np.fromfile + cv2.imdecode 兜底

另外运行期还有两件事（在 videonote/core/avatar.py 里处理，不需要这里管）：
  - Wav2Lip 内部 shell 调 ffmpeg，而本机 ffmpeg 来自 imageio-ffmpeg（名字不是 ffmpeg.exe）
  - 写工作区之外的目录会被权限拦
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "data" / "_tmp" / "Wav2Lip"
WEIGHTS = REPO / "checkpoints" / "wav2lip_gan.pth"
S3FD = REPO / "face_detection" / "detection" / "sfd" / "s3fd.pth"

GIT_URL = "https://github.com/Rudrabha/Wav2Lip"
# 官方权重在 Google Drive（被墙），这两个镜像实测可用
URL_WAV2LIP = "https://hf-mirror.com/Nekochu/Wav2Lip/resolve/main/wav2lip_gan.pth"
URL_S3FD = "https://hf-mirror.com/rippertnt/wav2lip/resolve/main/s3fd.pth"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def ensure_repo() -> None:
    step("1) Wav2Lip 代码")
    if (REPO / "inference.py").exists():
        print(f"   已存在：{REPO}")
        return
    REPO.parent.mkdir(parents=True, exist_ok=True)
    r = run(["git", "clone", "--depth", "1", GIT_URL, str(REPO)])
    if r.returncode != 0:
        raise SystemExit(f"克隆失败：{(r.stderr or '')[-400:]}")
    print(f"   已克隆到 {REPO}")


def ensure_weight(path: Path, url: str, label: str) -> None:
    if path.exists() and path.stat().st_size > 10 * 1024 * 1024:
        print(f"   {label} 已存在（{path.stat().st_size / 1048576:.1f}MB）")
        return
    print(f"   下载 {label} …（约 {(path.stat().st_size if path.exists() else 0) / 1048576:.0f}MB 已有）")
    path.parent.mkdir(parents=True, exist_ok=True)
    # curl 支持断点续传（-C -），镜像偶发断流时可以接着下
    r = run(["curl", "-L", "--fail", "--retry", "5", "--retry-delay", "3",
             "-C", "-", "--max-time", "3600", "-o", str(path), url])
    if r.returncode != 0 or not path.exists():
        raise SystemExit(f"下载 {label} 失败：{r.returncode} {(r.stderr or '')[-300:]}（可重跑，会续传）")
    print(f"   完成 {path.stat().st_size / 1048576:.1f}MB")


def patch_audio() -> None:
    step("2) 补丁：librosa 1.0 的 filters.mel 关键字参数")
    p = REPO / "audio.py"
    s = p.read_text(encoding="utf-8")
    old = "return librosa.filters.mel(hp.sample_rate, hp.n_fft, n_mels=hp.num_mels,"
    new = "return librosa.filters.mel(sr=hp.sample_rate, n_fft=hp.n_fft, n_mels=hp.num_mels,"
    if new in s:
        print("   已打过，跳过")
    elif old in s:
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("   已打补丁")
    else:
        print("   ⚠️ 没找到目标行（可能上游改了实现），请人工确认")


def patch_inference() -> None:
    step("3) 补丁：中文路径走 imdecode")
    p = REPO / "inference.py"
    lines = p.read_text(encoding="utf-8").splitlines()
    if any("cv2.imdecode(np.fromfile" in ln for ln in lines):
        print("   已打过，跳过")
        return

    idx = next((i for i, ln in enumerate(lines)
                if ln.strip() == "full_frames = [cv2.imread(args.face)]"), None)
    if idx is None:
        print("   ⚠️ 没找到 full_frames = [cv2.imread(args.face)]，请人工确认")
        return

    # 原文件用制表符缩进，这里必须跟着用制表符，否则会 TabError
    indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())]
    t = "\t"
    block = [
        f"{indent}_f = cv2.imread(args.face)",
        f"{indent}if _f is None:",
        f"{indent}{t}# OpenCV 在 Windows 上读不了含中文的路径，用 imdecode 兜底",
        f"{indent}{t}try:",
        f"{indent}{t}{t}_f = cv2.imdecode(np.fromfile(args.face, dtype=np.uint8), cv2.IMREAD_COLOR)",
        f"{indent}{t}except Exception:",
        f"{indent}{t}{t}_f = None",
        f"{indent}full_frames = [_f]",
    ]
    p.write_text("\n".join(lines[:idx] + block + lines[idx + 1:]) + "\n", encoding="utf-8")
    print("   已打补丁")


def main() -> int:
    ensure_repo()

    step("2) 权重（约 500MB）")
    ensure_weight(WEIGHTS, URL_WAV2LIP, "wav2lip_gan.pth")
    ensure_weight(S3FD, URL_S3FD, "s3fd.pth")

    patch_audio()
    patch_inference()

    step("4) 自检")
    from videonote.core import avatar

    ok, why = avatar.available()
    if ok:
        print("   ✅ 数字人可用")
        print("   试跑：avatar.render(照片, 配音.wav, 输出.mp4)")
    else:
        print(f"   ❌ 不可用：{why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
