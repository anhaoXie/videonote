"""媒体工具：探测元信息、抽音轨、调用 ffmpeg。"""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings
from ..models import MediaInfo

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".ts", ".m4v", ".mpg", ".mpeg", ".rmvb"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}


def ffmpeg_exe() -> Optional[str]:
    """优先用 .env 指定的，其次 imageio-ffmpeg 自带的，最后找 PATH。"""
    if settings.ffmpeg_path and Path(settings.ffmpeg_path).exists():
        return settings.ffmpeg_path
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("ffmpeg")


def run_ffmpeg(args: list[str], timeout: int = 3600) -> Tuple[int, str]:
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("找不到 ffmpeg（pip install imageio-ffmpeg 或设置 VN_FFMPEG）")
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "warning", "-y", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------
def probe(path: str | Path) -> MediaInfo:
    """用 PyAV 读元信息（faster-whisper 已经带了 PyAV，不额外依赖）。"""
    p = Path(path)
    info = MediaInfo(path=str(p), title=p.stem)
    try:
        import av

        with av.open(str(p)) as container:
            if container.duration:
                info.duration = container.duration / 1_000_000.0
            vstreams = [s for s in container.streams if s.type == "video"]
            astreams = [s for s in container.streams if s.type == "audio"]
            info.has_audio = bool(astreams)
            if vstreams:
                v = vstreams[0]
                info.width = int(v.codec_context.width or 0)
                info.height = int(v.codec_context.height or 0)
                if v.average_rate:
                    info.fps = float(v.average_rate)
                if not info.duration and v.duration and v.time_base:
                    info.duration = float(v.duration * v.time_base)
    except Exception:  # noqa: BLE001
        pass

    if info.duration <= 0:
        try:
            import cv2

            cap = cv2.VideoCapture(str(p))
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if fps > 0 and frames > 0:
                info.duration = frames / fps
            if not info.fps:
                info.fps = float(fps or 0)
            if not info.width:
                info.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                info.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            cap.release()
        except Exception:  # noqa: BLE001
            pass
    return info


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000,
                  progress=None) -> Path:
    """抽出 16kHz 单声道 wav —— 本地 Whisper 和云端 ASR 都吃这个。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    import av

    total = 0.0
    try:
        with av.open(str(src)) as container:
            total = (container.duration or 0) / 1_000_000.0
    except Exception:  # noqa: BLE001
        pass

    with av.open(str(src)) as container:
        astream = next((s for s in container.streams if s.type == "audio"), None)
        if astream is None:
            raise RuntimeError("该文件没有音频轨，无法做语音识别")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        with wave.open(str(dst), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            written = 0
            for frame in container.decode(astream):
                for rframe in resampler.resample(frame):
                    if rframe is None:
                        continue
                    wf.writeframes(bytes(rframe.planes[0]))
                    written += 1
                if progress and total > 0 and frame.time is not None:
                    progress(min(99.0, float(frame.time) / total * 100.0), "抽取音频")
            try:
                flushed = resampler.resample(None)
            except Exception:  # noqa: BLE001
                flushed = None
            for rframe in flushed or []:
                if rframe is not None:
                    wf.writeframes(bytes(rframe.planes[0]))
    if progress:
        progress(100.0, "音频就绪")
    return dst


def burn_subtitles(video: str | Path, subtitle: str | Path, out: str | Path,
                   font_size: int = 22) -> Path:
    """把字幕压进画面（硬字幕）。Windows 路径要转义，交给 ffmpeg 的 subtitles 滤镜。"""
    sub = str(subtitle).replace("\\", "/").replace(":", "\\:")
    style = f"FontSize={font_size},Outline=1,Shadow=0,MarginV=28"
    args = ["-i", str(video), "-vf", f"subtitles='{sub}':force_style='{style}'",
            "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)]
    code, output = run_ffmpeg(args)
    if code != 0 and not Path(out).exists():
        raise RuntimeError(f"压制字幕失败: {output[-800:]}")
    return Path(out)


def extract_clip(video: str | Path, start: float, end: float, out: str | Path) -> Path:
    """按句切视频片段 —— 跟读句库 / 影子跟读回放会用到。"""
    args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", str(out)]
    code, output = run_ffmpeg(args)
    if code != 0 and not Path(out).exists():
        raise RuntimeError(f"视频切片失败: {output[-500:]}")
    return Path(out)


def extract_audio_clip(video: str | Path, start: float, end: float, out: str | Path,
                       bitrate: str = "96k") -> Path:
    """按句切音频片段（给 Anki 卡片用）。优先 mp3，没编解码器就退回 wav。"""
    out = Path(out)
    args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video), "-vn",
            "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", bitrate, str(out)]
    code, output = run_ffmpeg(args)
    if code == 0 and out.exists() and out.stat().st_size > 512:
        return out
    # 退回无损 wav（Anki 也支持）
    wav = out.with_suffix(".wav")
    args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video), "-vn",
            "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(wav)]
    code, output = run_ffmpeg(args)
    if code != 0 or not wav.exists():
        raise RuntimeError(f"音频切片失败: {output[-500:]}")
    return wav
