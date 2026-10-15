# -*- coding: utf-8 -*-
"""本地说话人分离：ECAPA 声纹 embedding + 层次聚类。完全离线，不需要 HF token。

流程：
    每句话 -> ECAPA-TDNN 提取 192 维声纹 -> 余弦距离 -> 层次聚类
    -> 自动判断有几个人（用轮廓系数挑 k，区分度不够就认定只有 1 个人）
    -> 标回每句话

对美剧跟读的意义：你能只看某个角色的台词来练（配合 UI 的说话人筛选），
也能一眼看出哪几句是同一个人的语气。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from ..config import settings
from ..core.audio import read_wav_float32, rms
from .base import Diarizer
from ..core.registry import register_provider

_ecapa = None
_lock = threading.Lock()
MIN_SEG_SECONDS = 0.35      # 太短的段声纹不可靠，跳过
MIN_RMS = 0.004             # 基本静音就跳过
WINDOW = 2.5                # 长句切成 2.5 秒的窗口分别提声纹（更抗「ASR 把几个人合成一句」）
HOP = 1.25                  # 窗口步长
MAX_WINDOWS = 14            # 单句最多取多少个窗口，防止超长句拖慢
MIN_WINDOW_SECONDS = 1.0


def _patch_speechbrain_copy() -> None:
    """让 speechbrain 用「复制」代替「符号链接」。

    Windows 没开开发者模式（或不是管理员）时创建符号链接会 WinError 1314，
    speechbrain 下载模型时会逐个文件 symlink，于是直接崩。
    它内部所有落盘都走 fetching.link_with_strategy，替换成复制即可。
    """
    import speechbrain.utils.fetching as sb_fetch

    if getattr(sb_fetch, "_videonote_patched", False):
        return

    def _copy(src, dst, local_strategy=None):  # noqa: ANN001
        import shutil
        from pathlib import Path as _P

        src_p, dst_p = _P(src), _P(dst)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if dst_p.exists() or dst_p.is_symlink():
            try:
                dst_p.unlink()
            except OSError:
                pass
        shutil.copy2(src_p, dst_p)
        return dst_p

    sb_fetch.link_with_strategy = _copy
    sb_fetch._videonote_patched = True


def load_ecapa():
    """加载并缓存 ECAPA 声纹模型（第一次会下载约 80MB，走 HF 镜像）。"""
    global _ecapa
    with _lock:
        if _ecapa is None:
            from speechbrain.inference.speaker import EncoderClassifier

            _patch_speechbrain_copy()
            savedir = settings.models_dir / "ecapa_model"
            savedir.mkdir(parents=True, exist_ok=True)
            _ecapa = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=str(savedir),
                run_opts={"device": "cpu"},
            )
        return _ecapa


def embed(wav, sample_rate: int = 16000):
    """从 float32 波形提声纹向量（已归一化）。"""
    import numpy as np
    import torch

    model = load_ecapa()
    tensor = torch.from_numpy(np.asarray(wav, dtype="float32")).unsqueeze(0)
    with torch.no_grad():
        emb = model.encode_batch(tensor)
    vec = emb.squeeze().cpu().numpy().astype("float32")
    n = float(np.linalg.norm(vec)) or 1.0
    return vec / n


def cluster(embs, num_speakers: Optional[int] = None):
    """余弦距离 + 平均连接层次聚类；k 自动选（轮廓系数）。"""
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    n = len(embs)
    if n == 0:
        return [], 0, None
    if n == 1 or (num_speakers is None and n < 3):
        return [0] * n, 1, None
    X = np.vstack(embs)

    if num_speakers and num_speakers >= 1:
        if num_speakers == 1:
            return [0] * n, 1, None
        lab = AgglomerativeClustering(n_clusters=min(num_speakers, n), metric="cosine",
                                      linkage="average").fit_predict(X)
        return list(map(int, lab)), min(num_speakers, n), None

    best_k, best_lab, best_score = 1, [0] * n, -1.0
    for k in range(2, min(6, n - 1) + 1):
        lab = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(X)
        if len(set(lab)) < 2:
            continue
        try:
            score = float(silhouette_score(X, lab, metric="cosine"))
        except Exception:  # noqa: BLE001
            continue
        if score > best_score:
            best_k, best_lab, best_score = k, list(map(int, lab)), score
    # 区分度太低 -> 认为只有一个人（避免把一个人的语气变化硬切成两个人）
    if best_score < 0.10:
        return [0] * n, 1, best_score
    return best_lab, best_k, best_score


def _windows(start: float, end: float) -> List[tuple]:
    """把一句话切成若干声纹窗口；短句就整句一个窗口。"""
    dur = end - start
    if dur <= WINDOW * 1.2:
        return [(start, end)]
    out: List[tuple] = []
    t = start
    while t < end - MIN_WINDOW_SECONDS and len(out) < MAX_WINDOWS:
        w_end = min(t + WINDOW, end)
        if w_end - t >= MIN_WINDOW_SECONDS:
            out.append((t, w_end))
        t += HOP
    if not out:
        out.append((start, end))
    return out


def label_map(labels: List[int]) -> Dict[int, str]:
    """把聚类编号按「首次出现顺序」映射成 说话人1/2/3…"""
    out: Dict[int, str] = {}
    for lab in labels:
        if lab not in out:
            out[lab] = f"说话人{len(out) + 1}"
    return out


class EcapaDiarizer(Diarizer):
    name = "local_ecapa"

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name, "engine": "speechbrain ECAPA + 层次聚类", "device": "cpu"}

    def diarize(self, audio_path: str, segments: List[dict],
                 num_speakers: Optional[int] = None,
                 progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
        if not segments:
            return {"assignments": {}, "speakers": [], "engine": self.name}

        wav = read_wav_float32(audio_path)
        total = len(segments)
        embs: List[Any] = []
        meta: List[Dict[str, Any]] = []      # 每个声纹窗口对应哪一句、什么时间段
        errors: List[str] = []

        for i, seg in enumerate(segments):
            if progress and i % 2 == 0:
                progress(5 + i / total * 70.0, f"提取声纹 {i + 1}/{total}")
            s_start, s_end = float(seg["start"]), float(seg["end"])
            if s_end - s_start < MIN_SEG_SECONDS:
                continue
            for w_start, w_end in _windows(s_start, s_end):
                chunk = wav[max(0, int(w_start * 16000)):int(w_end * 16000)]
                if len(chunk) < int(MIN_WINDOW_SECONDS * 16000) or rms(chunk) < MIN_RMS:
                    continue
                try:
                    embs.append(embed(chunk))
                    meta.append({"seg": int(seg["idx"]), "start": round(w_start, 2),
                                 "end": round(w_end, 2), "dur": round(w_end - w_start, 2)})
                except Exception as exc:  # noqa: BLE001 - 单窗口失败不该毁掉整次分析
                    if not errors:
                        import traceback

                        errors.append(f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
                    continue

        if not embs:
            note = "没有可用的语音片段（可能全是静音或太短）"
            if errors:
                note = f"声纹模型不可用：{errors[0]}"
            return {"assignments": {}, "speakers": [], "engine": self.name, "note": note, "errors": errors}

        if progress:
            progress(80, f"聚类分析（{len(embs)} 个声纹窗口）")
        labels, k, score = cluster(embs, num_speakers)
        names = label_map(labels)

        # 每个窗口 -> 说话人；再按「时长加权投票」决定整句归属
        votes: Dict[int, Dict[str, float]] = {}
        windows_out: List[Dict[str, Any]] = []
        for pos, m in enumerate(meta):
            name = names[labels[pos]]
            windows_out.append({**m, "label": name})
            votes.setdefault(m["seg"], {})
            votes[m["seg"]][name] = votes[m["seg"]].get(name, 0.0) + m["dur"]

        assignments: Dict[int, str] = {}
        for seg_i, tally in votes.items():
            assignments[seg_i] = max(tally.items(), key=lambda kv: kv[1])[0]

        # 没提到声纹的短句：跟随最近的前一句（否则跟随后一句）
        ordered = [int(s["idx"]) for s in segments]
        last = None
        for idx in ordered:
            if idx in assignments:
                last = assignments[idx]
            elif last:
                assignments[idx] = last
        last = None
        for idx in reversed(ordered):
            if idx in assignments:
                last = assignments[idx]
            elif last:
                assignments[idx] = last

        speakers: Dict[str, Dict[str, Any]] = {}
        for seg in segments:
            name = assignments.get(int(seg["idx"]))
            if not name:
                continue
            entry = speakers.setdefault(name, {"label": name, "count": 0, "talk_time": 0.0, "segments": []})
            entry["count"] += 1
            entry["talk_time"] = round(entry["talk_time"] + (float(seg["end"]) - float(seg["start"])), 2)
            entry["segments"].append(int(seg["idx"]))

        if progress:
            progress(100, f"识别出 {len(speakers)} 位说话人（聚类 k={k}，轮廓系数 {score:.3f}）"
                     if score is not None else f"识别出 {len(speakers)} 位说话人")
        return {
            "assignments": assignments,
            "speakers": sorted(speakers.values(), key=lambda x: -x["talk_time"]),
            "windows": windows_out,
            "clusters": k,
            "silhouette": round(score, 4) if score is not None else None,
            "embedded": len(embs),
            "engine": "ecapa+sklearn",
        }


class PyannoteDiarizer(Diarizer):
    """可选的 pyannote 方案（更准，但模型是 gated 的，需要 HF token 并接受协议）。

    想用：.env 里设 VN_HF_TOKEN=...，然后把任务参数 diarize_provider 改成 pyannote。
    没装/没 token 时会给出清晰报错，不影响本地 ECAPA 方案。
    """

    name = "pyannote"

    def describe(self) -> Dict[str, Any]:
        return {"provider": self.name, "engine": "pyannote.audio 3.1", "gated": True}

    def diarize(self, audio_path: str, segments: List[dict],
                 num_speakers: Optional[int] = None,
                 progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
        import os

        token = os.environ.get("VN_HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        if not token:
            raise RuntimeError(
                "pyannote 需要 HuggingFace token：先去 huggingface.co 接受 "
                "pyannote/speaker-diarization-3.1 的使用协议，然后 .env 里设 VN_HF_TOKEN=..."
            )
        try:
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError("没装 pyannote.audio：pip install pyannote.audio") from exc
        if progress:
            progress(10, "加载 pyannote 模型")
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
        if progress:
            progress(30, "说话人分离中")
        annotation = pipeline(audio_path, num_speakers=num_speakers) if num_speakers else pipeline(audio_path)
        turns = [(seg.start, seg.end, spk) for seg, _, spk in annotation.itertracks(yield_label=True)]

        assignments: Dict[int, str] = {}
        order: Dict[str, str] = {}
        for seg in segments:
            s_mid = (float(seg["start"]) + float(seg["end"])) / 2
            best, best_overlap = None, 0.0
            for a, b, spk in turns:
                overlap = max(0.0, min(float(seg["end"]), b) - max(float(seg["start"]), a))
                if overlap > best_overlap:
                    best, best_overlap = spk, overlap
            if best:
                if best not in order:
                    order[best] = f"说话人{len(order) + 1}"
                assignments[int(seg["idx"])] = order[best]
        speakers = {}
        for seg in segments:
            name = assignments.get(int(seg["idx"]))
            if not name:
                continue
            e = speakers.setdefault(name, {"label": name, "count": 0, "talk_time": 0.0, "segments": []})
            e["count"] += 1
            e["talk_time"] = round(e["talk_time"] + float(seg["end"]) - float(seg["start"]), 2)
            e["segments"].append(int(seg["idx"]))
        return {"assignments": assignments, "speakers": list(speakers.values()),
                "engine": "pyannote", "clusters": len(speakers)}


@register_provider("diarize", "local_ecapa")
def _make_ecapa() -> Diarizer:
    return EcapaDiarizer()


@register_provider("diarize", "pyannote")
def _make_pyannote() -> Diarizer:
    return PyannoteDiarizer()
