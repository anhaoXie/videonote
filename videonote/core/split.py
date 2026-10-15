# -*- coding: utf-8 -*-
"""按说话人拆分长句（v0.4 P0-b）。

问题
----
`pipeline/asr.py` 的 `_tidy()` 为了句子长度会把相邻短句并起来
（`gap <= 0.9s` 且总长 `<= 12s` 且上一句不是句末标点）。于是「A 说一句 + B 回一句」
经常被合成**同一个 segment**。

而说话人分离是**按整句**投票的（`providers/diarize_local.py`：把每个声纹窗口的时长
加到它所属句子的票箱，然后 `max(tally)`）。一句里有两个人的时候，时间占比大的那个赢，
另一个人的台词被永久归错——这就是 ROADMAP 里「已知限制 #3」。

解法（不需要重跑声纹模型）
------------------------
diarize 早就把**窗口级**时间线一起存进了 `diarization` artifact：

    "windows": [{"seg": 3, "start": 12.5, "end": 15.0, "dur": 2.5, "label": "说话人1"}, ...]

而每个 segment 的词都带 `{w, start, end, prob}`（见 `models.Segment.words`），
`_tidy()` 合并时用 `prev.words.extend(s.words)` 把它们保留了。于是：
把每个词映射到「重叠最长的那个窗口」的说话人 → 相邻同说话人的词归成一组
→ 得到真实的分句边界。

代价：句号会重排
----------------
拆句 = 句子重新编号，旧 `idx` 全部失效。所有按 seg_idx 存的东西都必须一起迁移：

    clips / scores / reviews / practice_log      见 migrate()
    transcript / transcript_text 产出            按新句子重写（免费）
    translation 产出（JSON，key=idx）            迁移到「最长的那一段」
    subtitles_* / video_hardsub 产出             作废，等下次任务重新生成
    data/projects/<pid>/seg_audio/*.mp3          清空（idx 已经指到别的句子了）

所以它做成**显式任务**（job kind `split_speakers`），不挂在 diarize 后面自动跑。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import db
from ..config import settings

# 切出来的子句短于这个秒数就并回邻居：切出「Yeah.」这种碎片没有跟读价值
MIN_SUB_SECONDS = 0.4

# 作废后重生成是免费的产出（不需要大模型 key），留着它们才是错的
STALE_ARTIFACTS = ("subtitles_srt", "subtitles_vtt", "video_hardsub")

_CJK_RANGES = ((0x3000, 0x303F), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
               (0xF900, 0xFAFF), (0xFF00, 0xFFEF))
_NO_SPACE_BEFORE = set(",.!?;:)]}»”’%…，。！？；：、）】」』》")


# --------------------------------------------------------------------------
# 文本重建
# --------------------------------------------------------------------------
def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _CJK_RANGES)


def join_words(words: Sequence[dict]) -> str:
    """把词拼回一句话。

    **不能**一律 `" ".join()`：Whisper 给中文时词里本来就不含空格，用空格拼会变成
    「这 一 块 这 个 部 分」，比不拆还糟；给英文时又必须靠空格分词。
    所以按「相邻两个词的首尾字符是不是 CJK / 标点」来决定插不插空格。
    """
    toks = [str(w.get("w") or "").strip() for w in words]
    toks = [t for t in toks if t]
    if not toks:
        return ""
    out = toks[0]
    for prev, cur in zip(toks, toks[1:]):
        need_space = not (_is_cjk(prev[-1]) or _is_cjk(cur[0]) or cur[0] in _NO_SPACE_BEFORE)
        out += (" " if need_space else "") + cur
    return out


# --------------------------------------------------------------------------
# 切分
# --------------------------------------------------------------------------
def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def speaker_at(windows: Sequence[dict], start: float, end: float, fallback: str = "") -> str:
    """与 [start,end) 重叠最长的窗口属于谁；全都不重叠就用 fallback。"""
    best, best_ov = fallback, 0.0
    for w in windows or ():
        try:
            ov = _overlap(start, end, float(w["start"]), float(w["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if ov > best_ov:
            label = str(w.get("label") or w.get("speaker") or "")
            if label:
                best, best_ov = label, ov
    return best


def _group_dur(words: Sequence[dict]) -> float:
    if not words:
        return 0.0
    try:
        return max(0.0, float(words[-1].get("end") or 0) - float(words[0].get("start") or 0))
    except (TypeError, ValueError):
        return 0.0


def _merge_short(groups: List[list], min_sub: float) -> List[Tuple[str, List[dict]]]:
    """把太短的组并进邻居，然后**再合并相邻的同标签组**。

    并进**前**一组（保持「这段是谁在说」不变）。如果第一组本身就太短，
    就并进后面那组。合并后那些词的说话人跟着邻居走——这跟原来「整句取占比最大者」
    是同一个取向，只是作用范围小得多。

    最后那次同标签合并不是可选的：A B A 三段里如果 B 太短被并进 A，
    就变成两个相邻的「说话人A」组，**句子会在一个并不存在的说话人边界上被切开**。
    """
    if len(groups) <= 1:
        return [(g[0], list(g[1])) for g in groups]
    out: List[list] = [[groups[0][0], list(groups[0][1])]]
    for label, words in groups[1:]:
        if _group_dur(words) < min_sub:
            out[-1][1].extend(words)
            continue
        out.append([label, list(words)])
    if len(out) > 1 and _group_dur(out[0][1]) < min_sub:
        out[1][1] = out[0][1] + out[1][1]
        out.pop(0)

    merged: List[list] = []
    for label, words in out:
        if merged and merged[-1][0] == label:
            merged[-1][1].extend(words)
        else:
            merged.append([label, list(words)])
    return [(label, words) for label, words in merged]


def split_segment(seg: dict, windows: Sequence[dict], min_sub: float = MIN_SUB_SECONDS) -> List[dict]:
    """把一个 segment 按说话人切成 1..n 段。

    切不动（没有词 / 没有窗口 / 只有一个人）就**原样返回长度为 1 的列表**——
    诚实降级，绝不猜。
    """
    words = [w for w in (seg.get("words") or [])
             if isinstance(w, dict) and str(w.get("w") or "").strip()]
    if not words or not windows:
        return [seg]

    cur = str(seg.get("speaker") or "")
    labeled: List[Tuple[dict, str]] = []
    for w in words:
        try:
            ws, we = float(w.get("start") or 0.0), float(w.get("end") or 0.0)
        except (TypeError, ValueError):
            ws, we = 0.0, 0.0
        if we <= ws:
            we = ws + 0.01
        label = speaker_at(windows, ws, we, cur) or cur
        cur = label
        labeled.append((w, label))

    groups: List[list] = []
    for w, label in labeled:
        if groups and groups[-1][0] == label:
            groups[-1][1].append(w)
        else:
            groups.append([label, [w]])
    groups = _merge_short(groups, min_sub)
    if len(groups) <= 1:
        return [seg]

    lo, hi = float(seg.get("start") or 0.0), float(seg.get("end") or 0.0)
    out: List[dict] = []
    for label, ws in groups:
        start = max(lo, float(ws[0].get("start") or lo))
        end = min(hi, float(ws[-1].get("end") or hi))
        if end <= start:
            end = min(hi, start + 0.05) if hi > start else start
        out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": join_words(ws),
            "speaker": label,
            "words": ws,
        })
    return out


def plan(segments: Sequence[dict], windows: Sequence[dict],
         min_sub: float = MIN_SUB_SECONDS) -> Tuple[List[dict], List[Tuple[int, int, int]]]:
    """算出拆句后的新句子表 + 迁移映射。

    mapping = `[(old_idx, new_first, new_count), ...]`：旧 idx 的子句占据新编号
    `[new_first, new_first + new_count)`。

    因为拆句只是「把一句切成连续几段」，不同旧句的区间互不重叠，
    所以这个映射在**句子层面是单射**——clips / reviews 上的
    `UNIQUE(project_id, seg_idx)` 不会因为重映射而冲突，不需要去重合并。
    """
    new_segments: List[dict] = []
    mapping: List[Tuple[int, int, int]] = []
    for seg in segments:
        old_idx = int(seg.get("idx", len(mapping)))
        first = len(new_segments)
        parts = split_segment(seg, windows, min_sub)
        for p in parts:
            s = dict(p)
            s["idx"] = len(new_segments)
            new_segments.append(s)
        mapping.append((old_idx, first, len(parts)))
    return new_segments, mapping


def changed_count(mapping: Sequence[Tuple[int, int, int]]) -> int:
    """有多少个旧句真的被切开了。"""
    return sum(1 for _, _, n in mapping if n > 1)


# --------------------------------------------------------------------------
# 选落位：没有时间信息的记录该落到哪一段
# --------------------------------------------------------------------------
def _pick_longest(new_segments: Sequence[dict], first: int, count: int) -> int:
    """评分/复习/流水没有自己的时间，落到该旧句里**最长**的子句。

    为什么是最长：那是原来那句话的主体，用户在它上面练的时间最久；
    落到随便一段只会让「这句话我练过」变成假信息。
    """
    best, best_dur = first, -1.0
    for i in range(first, first + count):
        dur = float(new_segments[i]["end"]) - float(new_segments[i]["start"])
        if dur > best_dur:
            best, best_dur = i, dur
    return best


def _pick_overlap(new_segments: Sequence[dict], first: int, count: int,
                  start: float, end: float) -> int:
    """句库有自己的 start/end，按重叠最长落位。

    这样 clip 自己的 start/end/text 完全不用动——它切好的音频/视频文件仍然有效，
    只是「对应哪一句」的编号更新了。
    """
    best, best_ov = first, -1.0
    for i in range(first, first + count):
        ov = _overlap(start, end, float(new_segments[i]["start"]), float(new_segments[i]["end"]))
        if ov > best_ov:
            best, best_ov = i, ov
    return best


# --------------------------------------------------------------------------
# 数据库备份 + 迁移
# --------------------------------------------------------------------------
def backup_db(tag: str = "") -> Optional[Path]:
    """改号前备份数据库。用 sqlite 自己的 backup API（直接 copy 文件会丢 WAL 里的内容）。"""
    try:
        src = db.connect()
        d = settings.data_dir / "backups"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dst = d / f"videonote_{stamp}{('_' + tag) if tag else ''}.db"
        out = sqlite3.connect(str(dst))
        try:
            src.backup(out)
        finally:
            out.close()
        return dst
    except (sqlite3.Error, OSError):
        return None


def migrate(pid: str, new_segments: Sequence[dict], mapping: Sequence[Tuple[int, int, int]],
            *, backup: bool = True) -> Dict[str, Any]:
    """落库：重写 segments，并把所有按 seg_idx 存的数据迁移到新编号。

    整件事在**一个事务**里完成，失败整体回滚（见 db.transaction 的说明）。

    每一行都按**主键 id** 定位目标，而不是 `WHERE seg_idx = 旧号`：
    后者有「刚挪过去的行又被下一条规则挪走」的经典原地改号 bug
    （旧 0 号的最长子句可能是新 1 号，而 1 号也是旧 1 号的来源）。
    """
    first = {int(old): (int(a), int(n)) for old, a, n in mapping}
    pick = {old: _pick_longest(new_segments, a, n) for old, (a, n) in first.items()}
    moved = {"segments": len(new_segments), "scores": 0, "reviews": 0,
             "clips": 0, "practice": 0, "split": changed_count(mapping)}
    backup_path = backup_db(pid) if backup else None

    with db.transaction() as conn:
        # 1) segments 整表重写（idx 顺序变了，逐行 UPDATE 反而更容易错）
        conn.execute("DELETE FROM segments WHERE project_id=?", (pid,))
        conn.executemany(
            "INSERT INTO segments (project_id,idx,start,end,text,speaker,words)"
            " VALUES (?,?,?,?,?,?,?)",
            [(pid, int(s["idx"]), float(s["start"]), float(s["end"]), s.get("text") or "",
              s.get("speaker") or "",
              json.dumps(s.get("words") or [], ensure_ascii=False))
             for s in new_segments],
        )

        # 2) 评分：按 id 改号
        for row in conn.execute("SELECT id, seg_idx FROM scores WHERE project_id=?", (pid,)).fetchall():
            oi = int(row["seg_idx"])
            if oi in pick and pick[oi] != oi:
                conn.execute("UPDATE scores SET seg_idx=? WHERE id=?", (pick[oi], row["id"]))
                moved["scores"] += 1

        # 3) 复习队列
        for row in conn.execute("SELECT id, seg_idx FROM reviews WHERE project_id=?", (pid,)).fetchall():
            oi = int(row["seg_idx"])
            if oi in pick and pick[oi] != oi:
                conn.execute("UPDATE reviews SET seg_idx=? WHERE id=?", (pick[oi], row["id"]))
                moved["reviews"] += 1

        # 4) 训练流水
        for row in conn.execute("SELECT id, seg_idx FROM practice_log WHERE project_id=?", (pid,)).fetchall():
            oi = int(row["seg_idx"])
            if oi in pick and pick[oi] != oi:
                conn.execute("UPDATE practice_log SET seg_idx=? WHERE id=?", (pick[oi], row["id"]))
                moved["practice"] += 1

        # 5) 句库：按 clip 自己的时间区间落位（切片文件继续有效）
        for row in conn.execute("SELECT id, seg_idx, start, end FROM clips WHERE project_id=?", (pid,)).fetchall():
            oi = int(row["seg_idx"])
            if oi not in first:
                continue
            a, n = first[oi]
            tgt = _pick_overlap(new_segments, a, n, float(row["start"] or 0), float(row["end"] or 0))
            if tgt != oi:
                conn.execute("UPDATE clips SET seg_idx=? WHERE id=?", (tgt, row["id"]))
                moved["clips"] += 1

    # 事务外：文件类产出
    moved["backup"] = str(backup_path) if backup_path else ""
    moved["cache_cleared"] = clear_segment_cache(pid)
    moved["translation"] = remap_translation(pid, pick)
    moved["transcript"] = rewrite_transcript(pid, new_segments)
    moved["stale"] = drop_stale_artifacts(pid)
    return moved


def clear_segment_cache(pid: str) -> int:
    """清掉 `seg_audio/seg_<idx>.mp3` 缓存。

    这是最容易漏的一处：文件还在，但 idx 已经指到别的句子了，于是「听原声」
    会播放**别人的声音**——而且不报错。
    """
    d = settings.project_dir(pid) / "seg_audio"
    if not d.exists():
        return 0
    n = len(list(d.glob("*.mp3")))
    shutil.rmtree(d, ignore_errors=True)
    return n


def remap_translation(pid: str, pick: Dict[int, int]) -> int:
    """译文 JSON（key = seg_idx 字符串）跟着改号。

    一个旧句被拆成几段后，旧译文只归给**最长的那一段**。把整句译文复制到每个碎片上
    更糟——每段都会显示一整句的翻译。想让每段都有自己的译文，重跑一次「翻译」。
    """
    art = db.get_artifact(pid, "translation")
    if not art or not art.get("path"):
        return 0
    p = Path(art["path"])
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    out: Dict[str, Any] = {}
    for k, v in data.items():
        try:
            oi = int(k)
        except (TypeError, ValueError):
            continue
        if oi in pick:
            out[str(pick[oi])] = v
    if not out:
        return 0
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = dict(art.get("meta") or {})
    meta["split_stale"] = True
    meta["note"] = "拆句后译文只保留在最长的那一段；重跑「翻译」可得到每段各自的译文"
    db.put_artifact(pid, "translation", str(p), kind=art.get("kind") or "json", meta=meta)
    return len(out)


def rewrite_transcript(pid: str, new_segments: Sequence[dict]) -> bool:
    """按新句子重写 transcript.json 与 transcript_text（都不需要大模型 key）。"""
    ok = False
    art = db.get_artifact(pid, "transcript")
    if art and art.get("path"):
        p = Path(art["path"])
        try:
            data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["segments"] = [
            {"idx": int(s["idx"]), "start": float(s["start"]), "end": float(s["end"]),
             "text": s.get("text") or "", "speaker": s.get("speaker") or None,
             "words": s.get("words") or []}
            for s in new_segments
        ]
        meta = dict(art.get("meta") or {})
        meta["segments"] = len(new_segments)
        meta["split_stale"] = True
        try:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            db.put_artifact(pid, "transcript", str(p), kind=art.get("kind") or "json", meta=meta)
            ok = True
        except OSError:
            pass

    art2 = db.get_artifact(pid, "transcript_text")
    if art2 and art2.get("path"):
        p2 = Path(art2["path"])
        lines = [f"[{_ts(s['start'])} - {_ts(s['end'])}] {s.get('text') or ''}" for s in new_segments]
        try:
            p2.write_text("\n".join(lines), encoding="utf-8")
            ok = True
        except OSError:
            pass
    return ok


def _ts(seconds: float) -> str:
    m, sec = divmod(max(0.0, float(seconds)), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def drop_stale_artifacts(pid: str) -> List[str]:
    """作废字幕/压制产出：重新生成是免费的，留着才是错的（cue 的句子分组已经变了）。"""
    gone: List[str] = []
    for key in STALE_ARTIFACTS:
        art = db.get_artifact(pid, key)
        if not art:
            continue
        path = art.get("path") or ""
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        db.delete_artifact(pid, key)
        gone.append(key)
    return gone


def remap_diarization(pid: str, new_segments: Sequence[dict]) -> bool:
    """把 diarization 产出里的 `assignments` / `windows[].seg` 换成新编号。

    不改的话，artifact 里会留着一份指向旧编号的说话人表——它是「文档」，
    文档说谎比没有文档更糟。
    """
    art = db.get_artifact(pid, "diarization")
    if not art or not art.get("path"):
        return False
    p = Path(art["path"])
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    def _at(t: float) -> Optional[int]:
        """t 落在哪个新句子里（按中点找，找不到就 None）。"""
        for s in new_segments:
            if float(s["start"]) <= t < float(s["end"]):
                return int(s["idx"])
        return None

    old_assign = data.get("assignments") or {}
    new_assign: Dict[str, str] = {}
    for s in new_segments:
        label = str(s.get("speaker") or "")
        if label:
            new_assign[str(s["idx"])] = label
    if not new_assign:
        new_assign = {str(k): str(v) for k, v in old_assign.items()}
    data["assignments"] = new_assign
    data["assignments_source"] = "split_speakers 重映射"

    for w in data.get("windows") or []:
        if not isinstance(w, dict):
            continue
        try:
            mid = (float(w["start"]) + float(w["end"])) / 2
        except (KeyError, TypeError, ValueError):
            continue
        idx = _at(mid)
        if idx is not None:
            w["seg"] = idx

    meta = dict(art.get("meta") or {})
    meta["split_stale"] = True
    meta["segments"] = len(new_segments)
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return False
    db.put_artifact(pid, "diarization", str(p), kind=art.get("kind") or "json", meta=meta)
    return True
