# -*- coding: utf-8 -*-
"""coursegen —— 把一份文本 / 一本书变成「分集教学视频」。

设计原则（为什么这么切）：
    一条视频 = 若干「一句口播 + 一张画面」的片段顺序拼接。
    所以最内核的原子是 Line(say, slide)。大纲、脚本都只是在生产 Line。
    只要 Line 能稳定变成 mp4 片段，分集、系列都只是循环而已。

    - 大纲/脚本用大模型（可降级：没 key 时按文本切块）
    - 配音用本地 espeak（已有 tts 模块）——机器人味重，正式版建议换 edge-tts
    - 画面用无头 Chrome 把 HTML 渲成 1920x1080 PNG（字全、排版可控、无需前端框架）
    - 合成用项目自带的 ffmpeg（imageio-ffmpeg）

数字人（照片级口型）是**独立的一层**，接口留在这里（AVATAR_HOOK），
不让它拖住前面整条链：没有它照样能出可看的教学视频。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

W, H = 1920, 1080
FPS = 25

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Slide:
    kind: str = "bullets"           # title | bullets | quote | code
    title: str = ""
    bullets: List[str] = field(default_factory=list)
    note: str = ""                  # 教师备注（不上画面，只给脚本用）


@dataclass
class Line:
    say: str = ""                   # 口播台词
    slide: Slide = field(default_factory=Slide)
    audio: Optional[Path] = None
    seconds: float = 0.0


# ---------------------------------------------------------------------------
# 1. 大纲 / 脚本（大模型；没 key 时降级）
# ---------------------------------------------------------------------------
OUTLINE_SYS = "你是课程设计师。输出必须简洁、可教，避免把目录当内容。"
OUTLINE_PROMPT = """把下面的素材设计成 {episodes} 集教学视频，每集 {minutes} 分钟。

要求：
- 每集有明确的教学目标，3-5 个知识点；集与集之间递进，不要重复。
- 每个知识点要能「讲清楚」而不是只说标题。
- 只输出 JSON，不要解释：
{{"title":"课程名","episodes":[{{"no":1,"title":"分集标题","goal":"这一集要让人学会什么","points":["知识点","知识点"]}}]}}

素材：
{body}
"""

SCRIPT_SYS = "你是把知识讲清楚的口播撰稿人。句子短、口语化、有具体例子，不要念稿腔。"
SCRIPT_PROMPT = """为下面这一集写口播脚本。

{episode}

要求：
- 拆成 8-14 个镜头，每个镜头是「一两句口播 + 这一屏画面」。
- 每个镜头必须有具体的类比或例子，不要空话。
- 说出口的话要短（每句 40 字以内），适合配音和字幕。
- 画面用 bullets（3-5 条要点，每条 16 字以内）或 quote（一句话大字）或 code（代码/公式）。
- 只输出 JSON：
{{"lines":[{{"say":"口播台词","slide":{{"kind":"bullets","title":"这一屏的标题","bullets":["要点","要点"]}}}}]}}

素材摘录：
{source}
"""


def _provider():
    from ..core import registry
    from ..providers.base import get_llm_provider

    # 独立脚本（不经 app 启动）时注册中心是空的，必须先自动发现，
    # 否则 get_llm_provider 会抛 KeyError「未注册的 LLM provider」。
    registry.autodiscover()
    return get_llm_provider()


def plan_outline(source: str, episodes: int = 4, minutes: int = 3) -> Dict[str, Any]:
    """素材 -> 分集大纲。大模型不可用时按字符切块降级（保证流程能走通）。"""
    try:
        p = _provider()
        if getattr(p, "available", True):
            body = source[:6000]
            return p.complete_json(
                OUTLINE_PROMPT.format(episodes=episodes, minutes=minutes, body=body),
                system=OUTLINE_SYS, retries=2,
            )
    except Exception as exc:
        # 降级是设计好的兜底，但**不能静默**：用户必须知道为什么出来的东西很粗糙
        print(f"    [降级] 大模型大纲不可用：{type(exc).__name__}: {str(exc)[:160]}")
    # 降级：等分成 N 块，每块当一集——不聪明，但不会卡死
    chunk = max(1, len(source) // max(1, episodes))
    eps = []
    for i in range(episodes):
        part = source[i * chunk : (i + 1) * chunk]
        eps.append({
            "no": i + 1,
            "title": f"第 {i + 1} 集",
            "goal": "按原文顺序讲解",
            "points": [ln.strip() for ln in part.splitlines() if ln.strip()][:4] or ["原文内容"],
        })
    return {"title": "未命名课程", "episodes": eps}


def write_script(episode: Dict[str, Any], source: str = "") -> List[Line]:
    """一集大纲 -> Line 列表。大模型不可用时把 points 直接变成要点屏。"""
    try:
        p = _provider()
        if getattr(p, "available", True):
            ep_txt = json.dumps(episode, ensure_ascii=False)
            data = p.complete_json(
                SCRIPT_PROMPT.format(episode=ep_txt, source=source[:4000]),
                system=SCRIPT_SYS, retries=2,
            )
            lines: List[Line] = []
            for item in (data.get("lines") or []):
                sl = item.get("slide") or {}
                lines.append(Line(
                    say=str(item.get("say") or "").strip(),
                    slide=Slide(
                        kind=str(sl.get("kind") or "bullets"),
                        title=str(sl.get("title") or ""),
                        bullets=[str(b) for b in (sl.get("bullets") or [])],
                        note=str(sl.get("note") or ""),
                    ),
                ))
            if lines:
                return lines
    except Exception as exc:
        print(f"    [降级] 大模型脚本不可用：{type(exc).__name__}: {str(exc)[:160]}")
    # 降级：一屏一知识点
    lines = [Line(say=f"这一集我们讲{episode.get('title', '这个主题')}。",
                  slide=Slide(kind="title", title=str(episode.get("title", "")),
                              bullets=[str(episode.get("goal", ""))]))]
    for pt in (episode.get("points") or []):
        lines.append(Line(say=str(pt), slide=Slide(kind="quote", title=str(pt))))
    return lines


# ---------------------------------------------------------------------------
# 2. 画面：HTML -> PNG（无头 Chrome）
# ---------------------------------------------------------------------------
SLIDE_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  @page {{ size: {w}px {h}px; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html,body {{ margin:0; padding:0; width:{w}px; height:{h}px; }}
  body {{
    font-family: "Microsoft YaHei","PingFang SC",sans-serif;
    background:#0f1115; color:#f5f6f7; display:flex; flex-direction:column;
    padding:90px 120px; overflow:hidden;
  }}
  .kicker {{ font-size:26px; letter-spacing:6px; color:#8b93a1; margin-bottom:34px; }}
  h1 {{ font-size:{title_size}px; line-height:1.28; margin:0 0 46px; font-weight:700; }}
  .quote {{ font-size:86px; line-height:1.4; font-weight:600; }}
  ul {{ margin:0; padding-left:44px; }}
  li {{ font-size:{body_size}px; line-height:1.62; margin:22px 0; color:#d7dbe2; }}
  li::marker {{ color:#5b8def; }}
  .bar {{ margin-top:auto; height:6px; background:linear-gradient(90deg,#5b8def,#8b5cf6); width:180px; border-radius:3px; }}
  .foot {{ margin-top:26px; font-size:24px; color:#6b7280; }}
</style></head><body>
<div class="kicker">{kicker}</div>
<h1>{title}</h1>
{body}
<div class="bar"></div>
<div class="foot">{foot}</div>
</body></html>"""


def _chrome() -> str:
    for c in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(c).exists():
            return c
    raise RuntimeError("找不到 Chrome；画面渲染需要它（--headless 截图）")


def _render_slide_chrome(slide: Slide, out_png: Path, kicker: str = "", foot: str = "") -> Path:
    """（旧实现，保留作参考）用无头 Chrome 把 HTML 渲成 PNG。

    实测：从 Python 用 subprocess 调 Chrome 截图在本机会崩
    （exit 2147483651 / 加 --no-sandbox 后变 21，均无输出），
    同样的命令在 PowerShell 里却正常——是 Chrome 沙箱与父进程的冲突。
    产品里不能依赖这种不确定性，所以正式实现改用 Pillow 直接绘制。
    """
    title = slide.title or ""
    if slide.kind == "quote":
        body_html = f'<div class="quote">{_esc(title)}</div>'
        title_html = ""
    else:
        items = "".join(f"<li>{_esc(b)}</li>" for b in slide.bullets)
        body_html = f"<ul>{items}</ul>" if items else ""
        title_html = _esc(title)

    title_size = 104 if len(title) <= 16 else (84 if len(title) <= 30 else 66)
    body_size = 52 if slide.kind != "quote" else 62
    html = SLIDE_HTML.format(
        w=W, h=H, title_size=title_size, body_size=body_size,
        kicker=_esc(kicker or "TEACHING"), title=title_html, body=body_html,
        foot=_esc(foot),
    )
    out_png = Path(out_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = out_png.with_suffix(".html")
    tmp_html.write_text(html, encoding="utf-8")
    uri = "file:///" + str(tmp_html.resolve()).replace("\\", "/")
    # 注意：本机 tempfile.gettempdir() 会返回当前目录（相对路径），Chrome 拿到
    # 相对的 --user-data-dir 会直接崩（exit 2147483651）。所以一律用绝对路径，
    # 而且 profile 就放在产出目录旁边，不依赖系统临时目录。
    profile = (out_png.parent / "_chrome_profile").resolve()
    r = subprocess.run(
        [_chrome(), "--headless=new", "--disable-gpu", "--no-first-run",
         f"--user-data-dir={profile}", f"--window-size={W},{H}",
         "--hide-scrollbars", "--virtual-time-budget=3000",
         f"--screenshot={out_png}", uri],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0 or not out_png.exists():
        tail = (r.stderr or b"")[-600:].decode("utf-8", "replace")
        raise RuntimeError(f"Chrome 渲染画面失败（exit {r.returncode}）：{tail}")
    return out_png


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# 3. 配音：espeak（本地）
# ---------------------------------------------------------------------------
def _voice_for(text: str) -> str:
    """按文本选 espeak 语音。

    必须按语言选：默认的 en-us 会拿英文音素去读汉字，出来的是一串噪音
    （实测同一句中文，en-us 念 12.5 秒且不可懂，cmn 念 5.2 秒）。
    """
    return "cmn" if re.search(r"[\u4e00-\u9fff]", text or "") else "en-us"


_WARNED = {"espeak": False}


def tts_line(line: Line, out_wav: Path, wpm: int = 150, voice: str = "") -> Path:
    """一句口播 -> 一段 wav。

    首选 Windows 离线语音（中文音质明显好于 espeak，且不依赖网络）；
    不可用时退回 espeak-ng。
    """
    from ..core import tts as TTS

    p = TTS.synth_windows(line.say, out_wav, wpm=wpm)
    if p is not None:
        return p

    if not _WARNED["espeak"]:
        print("    [提示] Windows 离线语音不可用，本次改用 espeak 兜底（中文音质明显较差）")
        print("           自查：.venv\\Scripts\\python.exe tools\\test_voices.py")
        _WARNED["espeak"] = True

    import soundfile as sf

    if not TTS.available():
        raise RuntimeError("配音失败：Windows 语音与 espeak 都不可用")
    samples = TTS.synth(line.say, wpm=wpm, voice=voice or _voice_for(line.say))
    if samples is None or len(samples) == 0:
        raise RuntimeError(f"配音失败（空音频）：{line.say[:24]}")
    sf.write(str(out_wav), samples, 22050)
    return out_wav


# ---------------------------------------------------------------------------
# 4. 合成：ffmpeg
# ---------------------------------------------------------------------------
def _ffmpeg() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe

    return get_ffmpeg_exe()


def _run(cmd: List[str]) -> None:
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        tail = (r.stderr or b"")[-1200:].decode("utf-8", "replace")
        raise RuntimeError(f"ffmpeg 失败：{tail}")


def build_segment(png: Path, wav: Path, out_mp4: Path) -> Path:
    """一张图 + 一段配音 -> 一个视频片段（时长跟配音走）。"""
    _run([
        _ffmpeg(), "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png),
        "-i", str(wav),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
        str(out_mp4),
    ])
    return out_mp4


def concat(segments: List[Path], out_mp4: Path) -> Path:
    if not segments:
        raise RuntimeError("没有片段可合成")
    lst = out_mp4.with_suffix(".txt")
    # 必须用绝对路径：concat 分离器会把列表里的相对路径**相对列表文件所在目录**解析，
    # 于是 data/courses/demo/s000.mp4 会被拼成 demo/data/courses/demo/s000.mp4。
    lst.write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in segments),
                   encoding="utf-8")
    _run([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
          "-movflags", "+faststart", str(out_mp4)])
    return out_mp4


def probe(path: Path) -> Dict[str, Any]:
    """粗查时长/流（不依赖 ffprobe，直接读 ffmpeg -i 的 stderr）。"""
    r = subprocess.run([_ffmpeg(), "-i", str(path)], capture_output=True)
    txt = (r.stderr or b"").decode("utf-8", "replace")
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", txt)
    seconds = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 0.0
    return {
        "seconds": round(seconds, 2),
        "video": "Video:" in txt,
        "audio": "Audio:" in txt,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 2) if path.exists() else 0,
    }


# ---------------------------------------------------------------------------
# 5. 一集 = 一条视频
# ---------------------------------------------------------------------------
def make_episode(episode: Dict[str, Any], workdir: Path, source: str = "",
                 wpm: int = 150, progress=None,
                 lines: Optional[List[Line]] = None) -> Dict[str, Any]:
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    course_title = episode.get("course", "")
    lines = lines if lines is not None else write_script(episode, source)
    segs: List[Path] = []
    for i, ln in enumerate(lines):
        if progress:
            progress(i / max(1, len(lines)) * 100, f"第 {i + 1}/{len(lines)} 屏")
        png = workdir / f"s{i:03d}.png"
        wav = workdir / f"s{i:03d}.wav"
        mp4 = workdir / f"s{i:03d}.mp4"
        render_slide(ln.slide, png, kicker=course_title,
                     foot=f"{episode.get('title', '')} · {i + 1}/{len(lines)}")
        tts_line(ln, wav, wpm=wpm)
        build_segment(png, wav, mp4)
        segs.append(mp4)

    out = workdir / "episode.mp4"
    concat(segs, out)
    info = probe(out)
    info.update({"lines": len(lines), "path": str(out)})
    return info


# ---------------------------------------------------------------------------
# 6. 数字人（照片级口型）—— 独立一层，先留接口
# ---------------------------------------------------------------------------
AVATAR_HOOK = """
数字人这一层刻意不做进主链路。接入时的形状是：
    avatar_clip(portrait_png, audio_wav, out_mp4) -> Path
然后把 build_segment 的「静态图」换成「口型视频」，其余不变。

照片级口型的现实成本（纯 CPU，8 线程）：
    - 需要 face_alignment + Wav2Lip 权重（约 400MB，走 hf-mirror）
    - 3 分钟视频约 15–40 分钟推理；一季 10 集 = 一整晚
    - Wav2Lip 只重绘 96x96 的嘴部区域，大画面上偏糊；
      正片里建议做成「右下角小窗讲师」，观感最稳
"""


def avatar_available() -> bool:
    try:
        import face_alignment  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 画面渲染（正式实现）：Pillow 直接绘制
# ---------------------------------------------------------------------------
_BG = (15, 17, 21)
_FG = (245, 246, 247)
_DIM = (139, 147, 161)
_BODY = (215, 219, 226)
_FOOT = (107, 114, 128)
_ACCENT = (91, 141, 239)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    names = ("msyhbd.ttc", "msyh.ttc") if bold else ("msyh.ttc", "msyhbd.ttc")
    for n in names:
        p = Path(r"C:\Windows\Fonts") / n
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: float) -> List[str]:
    """按字符换行（中文没有空格，按词换行会断错）。"""
    out: List[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                if cur:
                    out.append(cur)
                cur = ch
        out.append(cur)
    return out


def render_slide(slide: Slide, out_png: Path, kicker: str = "", foot: str = "") -> Path:
    """把一屏画面渲染成 1920x1080 PNG（Pillow）。

    为什么不走浏览器：Chrome 从 Python 子进程调起时沙箱会崩，见
    _render_slide_chrome 的说明。Pillow 没有这个不确定性，而且更快
    （一屏约 50ms，浏览器要 1-2 秒）。
    """
    from PIL import Image, ImageDraw

    out_png = Path(out_png).resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), _BG)
    d = ImageDraw.Draw(img)
    pad = 120
    maxw = W - pad * 2
    y = 96

    f_kick = _font(26)
    d.text((pad, y), (kicker or "TEACHING"), font=f_kick, fill=_DIM)
    y += 70

    # 标题
    if slide.kind == "quote":
        f_title = _font(84, bold=True)
    else:
        f_title = _font(76 if len(slide.title or "") <= 18 else 58, bold=True)
    if slide.title and slide.kind != "quote":
        for ln in _wrap(d, slide.title, f_title, maxw)[:3]:
            d.text((pad, y), ln, font=f_title, fill=_FG)
            y += int(f_title.size * 1.34)
        y += 44

    # 正文
    if slide.kind == "quote":
        d.rectangle([pad, y, pad + 8, y + 250], fill=_ACCENT)
        for ln in _wrap(d, slide.title, f_title, maxw - 60)[:4]:
            d.text((pad + 40, y), ln, font=f_title, fill=_FG)
            y += int(f_title.size * 1.4)
    else:
        f_body = _font(46)
        for b in (slide.bullets or [])[:5]:
            d.ellipse([pad, y + 18, pad + 13, y + 31], fill=_ACCENT)
            for ln in _wrap(d, b, f_body, maxw - 56):
                d.text((pad + 40, y), ln, font=f_body, fill=_BODY)
                y += int(f_body.size * 1.44)
            y += 20

    # 强调条 + 页脚
    d.rectangle([pad, H - 150, pad + 180, H - 144], fill=_ACCENT)
    if foot:
        d.text((pad, H - 118), foot, font=_font(24), fill=_FOOT)

    img.save(out_png, "PNG")
    return out_png
