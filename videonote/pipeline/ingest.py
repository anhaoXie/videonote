# -*- coding: utf-8 -*-
"""ingest —— 把视频弄进来：本地文件 / 视频链接（yt-dlp）-> 统一成项目内的 media 文件。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from ..core.media import probe, ffmpeg_exe
from ..core.registry import register_stage
from ..core.stages import Stage, StageContext
from ..models import dumps


@register_stage
class IngestStage(Stage):
    name = "ingest"
    label = "导入媒体"
    description = "本地文件直接归位；链接用 yt-dlp 下载（支持 B站/YouTube 等 1000+ 站点）"
    produces = "media_file"

    def run(self, ctx: StageContext) -> Dict[str, Any]:
        params = ctx.params
        source_type = (params.get("source_type") or ctx.project.get("source_type") or "file").lower()
        src = params.get("source") or params.get("path") or ctx.project.get("source_uri") or ""
        media_dir = ctx.dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        ctx.set_progress(2, "准备导入")
        if source_type == "url":
            target = self._download(ctx, str(src), media_dir)
        else:
            target = self._copy_local(ctx, str(src), media_dir)

        ctx.set_progress(88, "读取媒体信息")
        info = probe(target)
        if info.duration <= 0:
            ctx.log("警告：无法读取时长，后续按实际识别结果处理", "warn")
        info.source_type = source_type
        info.source_uri = str(src)
        if not info.title:
            info.title = Path(target).stem

        ctx.save_json("media_info", info.to_dict(), "media_info.json", meta={"duration": info.duration})
        # 主媒体文件就放在项目 media/ 目录里，直接登记（不要用 artifact_path，那会得到不存在的路径）
        ctx.put_artifact("media_file", target, kind="media",
                         meta={"duration": info.duration, "size": Path(target).stat().st_size})
        from .. import db

        db.update_project(
            ctx.project_id,
            media_path=str(target),
            duration=info.duration,
            title=ctx.project.get("title") or info.title,
            status="ingested",
        )
        ctx.set_progress(100, f"媒体就绪：{info.duration:.0f}s {info.width}x{info.height}")
        return {"media_path": str(target), "duration": info.duration, "has_audio": info.has_audio}

    # ------------------------------------------------------------------
    def _copy_local(self, ctx: StageContext, src: str, media_dir: Path) -> Path:
        if not src:
            raise ValueError("没有拿到文件路径（source 为空）")
        p = Path(src)
        if not p.exists():
            # 可能已经在前一步放到项目 media 目录里了
            candidate = media_dir / p.name
            if candidate.exists():
                return candidate
            raise FileNotFoundError(f"文件不存在: {src}")
        target = media_dir / p.name
        if p.resolve() != target.resolve():
            ctx.set_progress(20, f"复制文件 {p.name}（{p.stat().st_size/1024/1024:.1f} MB）")
            shutil.copy2(p, target)
        return target

    def _download(self, ctx: StageContext, url: str, media_dir: Path) -> Path:
        from yt_dlp import YoutubeDL

        from ..config import settings

        if not url:
            raise ValueError("URL 为空")

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                pct = (got / total * 100) if total else 0
                ctx.set_progress(min(85.0, pct * 0.85),
                                 f"下载中 {pct:.0f}% · {got/1024/1024:.0f}MB/{total/1024/1024:.0f}MB")
            elif d.get("status") == "finished":
                ctx.set_progress(86, "下载完成，正在合并音视频")

        opts: Dict[str, Any] = {
            "outtmpl": str(media_dir / "%(title).80s.%(ext)s"),
            "format": "bv*+ba/b",          # 有分离音视频就合并，否则取最佳单流
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "retries": 3,
            "concurrent_fragment_downloads": 4,
        }
        exe = ffmpeg_exe()
        if exe:
            opts["ffmpeg_location"] = exe
        if settings.ytdlp_cookies:
            opts["cookiefile"] = settings.ytdlp_cookies

        ctx.set_progress(3, "解析链接…")
        with YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=True)
            if data and data.get("entries"):
                data = data["entries"][0]
            path = data.get("requested_downloads", [{}])[0].get("filepath") if data else None
            if not path:
                path = ydl.prepare_filename(data)
        p = Path(path)
        if not p.exists():
            # 合并后扩展名可能变了，扫一遍目录
            files = sorted(media_dir.glob("*"), key=lambda x: x.stat().st_size, reverse=True)
            if not files:
                raise RuntimeError("下载完成但找不到输出文件")
            p = files[0]
        title = (data or {}).get("title") or p.stem
        from .. import db

        db.update_project(ctx.project_id, title=title, meta={**ctx.project.get("meta", {}), "url_title": title,
                                                            "uploader": (data or {}).get("uploader", "")})
        ctx.log(f"下载完成: {p.name} <- {url}")
        return p
