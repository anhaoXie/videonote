# -*- coding: utf-8 -*-
"""数字人（照片级口型，Wav2Lip）—— **实验性，未接入主流程**。

⚠️ 这个方案已被否决，主流程改用 `videonote/core/cartoon.py` 的 2D 卡通讲解员。
否决理由（实测结论，别再走回头路）：
  1. 必须有真人照片；用户不上传就没有头像，而产品最关键的入口不能留空。
  2. Wav2Lip 的嘴部生成器只有 96×96，贴回大图后糊，观感「半真半假」，很难看。
  3. 网上找的图有版权风险，且平面图不会眨眼。

保留价值：以后若要做「上传自己的照片当讲师」这种可选真人模式，
下面整条链路是跑通过的（权重 / 补丁 / 编码坑都解决了），可直接复用。
启用前先跑 `tools/setup_avatar.py`。

以下是原始说明：
Wav2Lip 封装。

踩过的坑（都是真机上验证过的，别再走回头路）：

1. **OpenCV 读不了含中文的路径**。`cv2.imread(r"C:\\Users\\谢安豪\\...\\face.png")`
   返回 None，导致检测阶段拿到空数组报 `ims - [104,117,123]`。
   → 对 vendored 的 `inference.py` 打了补丁：imread 失败时用 `np.fromfile` +
   `cv2.imdecode` 兜底（比「复制到 ASCII 目录」可靠，也不受沙箱写权限限制）。

2. **Wav2Lip 内部用 `shell=True` 调 `ffmpeg`**，而本机 ffmpeg 来自 imageio-ffmpeg，
   文件名不是 `ffmpeg.exe`；且命令行里一旦有中文路径，经 cmd.exe 的 GBK 代码页会乱码。
   → 建一个 ASCII 别名目录把 exe 复制成 `ffmpeg.exe`；**混流不交给它做**，改为
   我们自己的 ffmpeg 以参数数组方式调用（不经 shell，Unicode 安全）。

3. **写工作区之外的目录会被权限拦**（`Error opening output ...: Permission denied`）。
   → 输出统一落在仓库内的 `temp/`，成功后搬走。

4. **librosa 1.0 把 `filters.mel` 改成纯关键字参数**
   → 对 vendored 的 `audio.py` 打过补丁（`sr=` / `n_fft=`）。

产物：一段与配音等长的说话头像视频（264×264 左右，Wav2Lip 生成器内部为 96×96）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[2]          # 仓库根
REPO = ROOT / "data" / "_tmp" / "Wav2Lip"
WEIGHTS = REPO / "checkpoints" / "wav2lip_gan.pth"
S3FD = REPO / "face_detection" / "detection" / "sfd" / "s3fd.pth"
FFMPEG_ALIAS_DIR = ROOT / "data" / "_tmp" / "bin"
RESULT_AVI = REPO / "temp" / "result.avi"           # inference.py 硬编码的中间产物


def available() -> Tuple[bool, str]:
    """能不能跑数字人？返回 (可用, 原因)。"""
    if not REPO.exists():
        return False, f"缺少 Wav2Lip 代码：{REPO}"
    if not WEIGHTS.exists():
        return False, f"缺少权重：{WEIGHTS.name}"
    if not S3FD.exists():
        return False, f"缺少人脸检测权重：{S3FD.name}"
    try:
        import librosa  # noqa: F401
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"依赖缺失：{type(exc).__name__}: {exc}"
    return True, ""


def _ffmpeg_alias() -> Path:
    """保证有个叫 ffmpeg.exe 的可执行文件（Wav2Lip 内部用 shell 调它）。"""
    import imageio_ffmpeg

    FFMPEG_ALIAS_DIR.mkdir(parents=True, exist_ok=True)
    alias = FFMPEG_ALIAS_DIR / "ffmpeg.exe"
    if not alias.exists():
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), alias)
    return FFMPEG_ALIAS_DIR


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _mux(video: Path, audio: Path, out: Path) -> Path:
    """把无声的 avi/mp4 与配音合到一起（参数数组调用，unicode 安全）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(), "-y",
        "-i", str(video),
        "-i", str(audio),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=1800)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"混流失败：{(proc.stderr or '')[-500:]}")
    return out


def render(face: Path, audio: Path, out_mp4: Path, timeout: int = 3600) -> Path:
    """把一张正面照片 + 一段配音合成为「会说话的头」。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(f"数字人不可用：{why}")

    face = Path(face).resolve()
    audio = Path(audio).resolve()
    out_mp4 = Path(out_mp4).resolve()
    if not face.exists():
        raise FileNotFoundError(f"找不到照片：{face}")
    if not audio.exists():
        raise FileNotFoundError(f"找不到音频：{audio}")

    token = uuid.uuid4().hex[:8]
    tmp_out = REPO / "temp" / f"avatar_{token}.mp4"
    tmp_out.parent.mkdir(parents=True, exist_ok=True)

    alias_dir = _ffmpeg_alias()
    env = dict(os.environ)
    env["PATH"] = str(alias_dir) + os.pathsep + env.get("PATH", "")

    before = RESULT_AVI.stat().st_mtime if RESULT_AVI.exists() else 0.0

    cmd = [
        sys.executable, "inference.py",
        "--checkpoint_path", str(WEIGHTS),
        "--face", str(face),
        "--audio", str(audio),
        "--outfile", str(tmp_out),
        "--static", "True",          # 注意：type=bool，必须显式给值
    ]
    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8",
                          errors="replace")

    try:
        # 首选：inference.py 自己混流成功（音频路径是 ASCII 时才可能）
        if proc.returncode == 0 and tmp_out.exists():
            out_mp4.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_out), str(out_mp4))
            return out_mp4

        # 退路：它已生成无声的 result.avi，我们自己混流
        fresh = RESULT_AVI.exists() and RESULT_AVI.stat().st_mtime > before
        if not fresh:
            tail = (proc.stderr or proc.stdout or "")[-800:]
            raise RuntimeError(f"Wav2Lip 失败（exit {proc.returncode}）：{tail}")

        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        return _mux(RESULT_AVI, audio, out_mp4)
    finally:
        try:
            tmp_out.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
