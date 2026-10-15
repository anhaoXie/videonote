"""SQLite 存储层。用原生 sqlite3 + 一行 SQL，方便你以后加表加字段。

设计原则：
  * projects   一个视频 = 一个 project
  * artifacts  每个阶段产出的东西（音频/转写/关键帧/笔记/字幕）都是 artifact，插件只认 key
  * segments   转写句子单独成表，播放器和跟读模式直接按时间查
  * jobs       任务队列 + 进度 + 日志
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from .config import settings
from .models import JobStatus, dumps, loads, new_id, now_iso

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    source_type TEXT DEFAULT 'file',
    source_uri  TEXT DEFAULT '',
    media_path  TEXT DEFAULT '',
    duration    REAL DEFAULT 0,
    status      TEXT DEFAULT 'created',
    meta        TEXT DEFAULT '{}',
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    key        TEXT NOT NULL,
    kind       TEXT DEFAULT 'file',
    path       TEXT DEFAULT '',
    meta       TEXT DEFAULT '{}',
    created_at TEXT,
    UNIQUE(project_id, key)
);

CREATE TABLE IF NOT EXISTS segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    start      REAL NOT NULL,
    end        REAL NOT NULL,
    text       TEXT DEFAULT '',
    speaker    TEXT DEFAULT '',
    words      TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_segments_project ON segments(project_id, idx);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    kind        TEXT NOT NULL,
    status      TEXT DEFAULT 'queued',
    progress    REAL DEFAULT 0,
    message     TEXT DEFAULT '',
    error       TEXT DEFAULT '',
    params      TEXT DEFAULT '{}',
    result      TEXT DEFAULT '{}',
    created_at  TEXT,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);

CREATE TABLE IF NOT EXISTS job_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL,
    ts      TEXT,
    level   TEXT DEFAULT 'info',
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON job_logs(job_id, id);

-- 跟读句库：收藏的句子 + 切好的音视频片段
CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    seg_idx       INTEGER NOT NULL,
    start         REAL DEFAULT 0,
    end           REAL DEFAULT 0,
    text          TEXT DEFAULT '',
    translation   TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    tags          TEXT DEFAULT '',
    review_count  INTEGER DEFAULT 0,
    last_reviewed TEXT,
    audio_path    TEXT DEFAULT '',
    video_path    TEXT DEFAULT '',
    created_at    TEXT,
    UNIQUE(project_id, seg_idx)
);
CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id, seg_idx);

-- 发音评分记录（每次跟读一条）
CREATE TABLE IF NOT EXISTS scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    seg_idx     INTEGER NOT NULL,
    score       REAL DEFAULT 0,
    accuracy    REAL DEFAULT 0,
    fluency     REAL DEFAULT 0,
    completeness REAL DEFAULT 0,
    target      TEXT DEFAULT '',
    transcript  TEXT DEFAULT '',
    detail      TEXT DEFAULT '{}',
    recording   TEXT DEFAULT '',
    mode        TEXT DEFAULT 'repeat',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_project ON scores(project_id, seg_idx, created_at);

-- 间隔重复复习队列（SM-2）：读错的句子自动回到你面前
CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    seg_idx       INTEGER NOT NULL,
    ease          REAL DEFAULT 2.5,
    interval_days REAL DEFAULT 0,
    reps          INTEGER DEFAULT 0,
    lapses        INTEGER DEFAULT 0,
    due_at        TEXT,
    last_at       TEXT,
    last_score    REAL DEFAULT 0,
    best_score    REAL DEFAULT 0,
    reason        TEXT DEFAULT '',
    status        TEXT DEFAULT 'new',
    created_at    TEXT,
    UNIQUE(project_id, seg_idx)
);
CREATE INDEX IF NOT EXISTS idx_reviews_due ON reviews(status, due_at);
CREATE INDEX IF NOT EXISTS idx_reviews_project ON reviews(project_id, status);

-- 复习/训练事件流水（用于统计：每天练了多少、连了几天）
CREATE TABLE IF NOT EXISTS practice_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    seg_idx    INTEGER DEFAULT -1,
    kind       TEXT DEFAULT 'repeat',
    score      REAL DEFAULT 0,
    seconds    REAL DEFAULT 0,
    day        TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_practice_day ON practice_log(day, project_id);

-- 最小对立对（ship/sheep）单词训练：两种模式各记一行
--   kind='listen' 听音辨词（选对没选对）
--   kind='say'    跟读对比（音素级判定）
CREATE TABLE IF NOT EXISTS pair_drills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id    TEXT NOT NULL,
    word       TEXT DEFAULT '',
    kind       TEXT DEFAULT 'say',
    correct    INTEGER DEFAULT 0,
    score      REAL DEFAULT 0,
    expected   TEXT DEFAULT '',
    heard      TEXT DEFAULT '',
    detail     TEXT DEFAULT '{}',
    day        TEXT DEFAULT '',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pair_drills ON pair_drills(pair_id, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_pair_drills_day ON pair_drills(day);

-- 单词卡复习队列（SM-2）。跟句子队列 reviews 分开存：
--   reviews 的身份是 (project_id, seg_idx)，单词两者都没有，硬塞进去只会弄脏句子语义。
--   UNIQUE(pair_id, word) —— 一张卡是「一个具体的词」而不是词对：
--   你可能读得对 sheep、读不对 ship，按词对调度会把这个差异抹掉。
CREATE TABLE IF NOT EXISTS word_reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id       TEXT NOT NULL,
    word          TEXT NOT NULL,
    ease          REAL DEFAULT 2.5,
    interval_days REAL DEFAULT 0,
    reps          INTEGER DEFAULT 0,
    lapses        INTEGER DEFAULT 0,
    due_at        TEXT,
    last_at       TEXT,
    last_score    REAL DEFAULT 0,
    best_score    REAL DEFAULT 0,
    reason        TEXT DEFAULT '',
    status        TEXT DEFAULT 'new',
    created_at    TEXT,
    UNIQUE(pair_id, word)
);
CREATE INDEX IF NOT EXISTS idx_word_reviews_due ON word_reviews(status, due_at);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.ensure_dirs()
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def init() -> None:
    connect()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------
def create_project(title: str, source_type: str = "file", source_uri: str = "", meta: Optional[dict] = None,
                   pid: Optional[str] = None) -> dict:
    pid = pid or new_id("p_")
    ts = now_iso()
    execute(
        "INSERT INTO projects (id,title,source_type,source_uri,media_path,duration,status,meta,created_at,updated_at)"
        " VALUES (?,?,?,?,'',0,'created',?,?,?)",
        (pid, title, source_type, source_uri, dumps(meta or {}), ts, ts),
    )
    return get_project(pid)  # type: ignore[return-value]


def get_project(pid: str) -> Optional[dict]:
    row = query_one("SELECT * FROM projects WHERE id=?", (pid,))
    if row:
        row["meta"] = loads(row.get("meta"), {})
    return row


def list_projects() -> list[dict]:
    rows = query("SELECT * FROM projects ORDER BY created_at DESC")
    for r in rows:
        r["meta"] = loads(r.get("meta"), {})
    return rows


def update_project(pid: str, **fields: Any) -> None:
    if not fields:
        return
    if "meta" in fields and isinstance(fields["meta"], dict):
        fields["meta"] = dumps(fields["meta"])
    fields["updated_at"] = now_iso()
    cols = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE projects SET {cols} WHERE id=?", (*fields.values(), pid))


def delete_project(pid: str) -> None:
    for sql in (
        "DELETE FROM segments WHERE project_id=?",
        "DELETE FROM artifacts WHERE project_id=?",
        "DELETE FROM jobs WHERE project_id=?",
        "DELETE FROM clips WHERE project_id=?",     # 句库跟着项目一起走，否则会留下孤儿卡片
        "DELETE FROM scores WHERE project_id=?",
        "DELETE FROM reviews WHERE project_id=?",
        "DELETE FROM practice_log WHERE project_id=?",
        "DELETE FROM projects WHERE id=?",
    ):
        execute(sql, (pid,))


def cleanup_orphans() -> dict:
    """清掉指向已删除项目的句库/评分（手动删过 data/projects 后也能自愈）。"""
    out = {}
    for table in ("clips", "scores", "reviews", "practice_log"):
        out[table] = execute(
            f"DELETE FROM {table} WHERE project_id NOT IN (SELECT id FROM projects)").rowcount or 0
    return out


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------
def put_artifact(pid: str, key: str, path: str = "", kind: str = "file", meta: Optional[dict] = None) -> None:
    execute(
        "INSERT INTO artifacts (project_id,key,kind,path,meta,created_at) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(project_id,key) DO UPDATE SET path=excluded.path, kind=excluded.kind,"
        " meta=excluded.meta, created_at=excluded.created_at",
        (pid, key, kind, str(path), dumps(meta or {}), now_iso()),
    )


def get_artifact(pid: str, key: str) -> Optional[dict]:
    row = query_one("SELECT * FROM artifacts WHERE project_id=? AND key=?", (pid, key))
    if row:
        row["meta"] = loads(row.get("meta"), {})
    return row


def list_artifacts(pid: str) -> list[dict]:
    rows = query("SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at", (pid,))
    for r in rows:
        r["meta"] = loads(r.get("meta"), {})
    return rows


def delete_artifact(pid: str, key: str) -> None:
    execute("DELETE FROM artifacts WHERE project_id=? AND key=?", (pid, key))


# --------------------------------------------------------------------------
# segments
# --------------------------------------------------------------------------
def replace_segments(pid: str, segments: list[dict]) -> None:
    execute("DELETE FROM segments WHERE project_id=?", (pid,))
    conn = connect()
    with _lock:
        conn.executemany(
            "INSERT INTO segments (project_id,idx,start,end,text,speaker,words) VALUES (?,?,?,?,?,?,?)",
            [
                (
                    pid,
                    int(s.get("idx", i)),
                    float(s.get("start", 0)),
                    float(s.get("end", 0)),
                    s.get("text", ""),
                    s.get("speaker") or "",
                    dumps(s.get("words") or []),
                )
                for i, s in enumerate(segments)
            ],
        )
        conn.commit()


def list_segments(pid: str) -> list[dict]:
    rows = query("SELECT idx,start,end,text,speaker,words FROM segments WHERE project_id=? ORDER BY idx", (pid,))
    for r in rows:
        r["words"] = loads(r.get("words"), [])
    return rows


def set_speakers(pid: str, assignments: dict) -> int:
    """把说话人标签写回句子（说话人分离的产出）。"""
    if not assignments:
        return 0
    conn = connect()
    with _lock:
        conn.executemany(
            "UPDATE segments SET speaker=? WHERE project_id=? AND idx=?",
            [(str(v), pid, int(k)) for k, v in assignments.items()],
        )
        conn.commit()
    return len(assignments)


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------
def create_job(pid: Optional[str], kind: str, params: Optional[dict] = None) -> dict:
    jid = new_id("j_")
    execute(
        "INSERT INTO jobs (id,project_id,kind,status,progress,message,params,created_at) VALUES (?,?,?,'queued',0,'',?,?)",
        (jid, pid, kind, dumps(params or {}), now_iso()),
    )
    return get_job(jid)  # type: ignore[return-value]


def get_job(jid: str) -> Optional[dict]:
    row = query_one("SELECT * FROM jobs WHERE id=?", (jid,))
    if row:
        row["params"] = loads(row.get("params"), {})
        row["result"] = loads(row.get("result"), {})
    return row


def list_jobs(pid: Optional[str] = None, limit: int = 50) -> list[dict]:
    if pid:
        rows = query("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (pid, limit))
    else:
        rows = query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    for r in rows:
        r["params"] = loads(r.get("params"), {})
        r["result"] = loads(r.get("result"), {})
    return rows


def update_job(jid: str, **fields: Any) -> None:
    if not fields:
        return
    for k in ("params", "result"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = dumps(fields[k])
    cols = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))


def claim_next_job() -> Optional[dict]:
    """原子地取出下一个排队任务。"""
    with _lock:
        conn = connect()
        row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE jobs SET status=?, started_at=?, progress=0, message='' WHERE id=?",
            (JobStatus.RUNNING, now_iso(), row["id"]),
        )
        conn.commit()
    return get_job(row["id"])


def cancel_job(jid: str) -> bool:
    job = get_job(jid)
    if not job or job["status"] not in (JobStatus.QUEUED, JobStatus.RUNNING):
        return False
    update_job(jid, status=JobStatus.CANCELED, message="已取消", finished_at=now_iso())
    return True


def is_canceled(jid: str) -> bool:
    row = query_one("SELECT status FROM jobs WHERE id=?", (jid,))
    return bool(row and row["status"] == JobStatus.CANCELED)


def add_log(jid: str, message: str, level: str = "info") -> None:
    execute("INSERT INTO job_logs (job_id,ts,level,message) VALUES (?,?,?,?)", (jid, now_iso(), level, message))


def list_logs(jid: str, limit: int = 500) -> list[dict]:
    return query("SELECT ts,level,message FROM job_logs WHERE job_id=? ORDER BY id LIMIT ?", (jid, limit))


def recover_orphan_jobs() -> int:
    """服务启动时，把上次崩溃残留的 running 任务标为失败，避免卡死。"""
    rows = query("SELECT id FROM jobs WHERE status='running'")
    for r in rows:
        update_job(r["id"], status=JobStatus.FAILED, error="服务重启，任务中断", finished_at=now_iso())
    return len(rows)


# --------------------------------------------------------------------------
# clips（跟读句库）
# --------------------------------------------------------------------------
CLIP_FIELDS = ("note", "tags", "review_count", "last_reviewed", "audio_path", "video_path",
               "text", "translation", "start", "end")


def upsert_clip(pid: str, seg_idx: int, **fields: Any) -> dict:
    existing = query_one("SELECT * FROM clips WHERE project_id=? AND seg_idx=?", (pid, seg_idx))
    if existing:
        if fields:
            update_clip(existing["id"], **fields)
        return get_clip(existing["id"])  # type: ignore[return-value]
    execute(
        "INSERT INTO clips (project_id,seg_idx,start,end,text,translation,note,tags,review_count,"
        "audio_path,video_path,created_at) VALUES (?,?,?,?,?,?,?,?,0,?,?,?)",
        (
            pid, seg_idx,
            float(fields.get("start", 0)), float(fields.get("end", 0)),
            fields.get("text", ""), fields.get("translation", ""), fields.get("note", ""),
            fields.get("tags", ""), fields.get("audio_path", ""), fields.get("video_path", ""),
            now_iso(),
        ),
    )
    row = query_one("SELECT * FROM clips WHERE project_id=? AND seg_idx=?", (pid, seg_idx))
    return row  # type: ignore[return-value]


def get_clip(cid: int) -> Optional[dict]:
    return query_one("SELECT * FROM clips WHERE id=?", (cid,))


def list_clips(pid: Optional[str] = None, tag: str = "") -> list[dict]:
    if pid:
        rows = query("SELECT * FROM clips WHERE project_id=? ORDER BY seg_idx", (pid,))
    else:
        rows = query("SELECT * FROM clips ORDER BY created_at DESC")
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or "")]
    return rows


def update_clip(cid: int, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if k in CLIP_FIELDS}
    if not fields:
        return
    cols = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE clips SET {cols} WHERE id=?", (*fields.values(), cid))


def delete_clip(cid: int) -> None:
    execute("DELETE FROM clips WHERE id=?", (cid,))


def touch_clip_review(cid: int) -> None:
    execute("UPDATE clips SET review_count=review_count+1, last_reviewed=? WHERE id=?", (now_iso(), cid))


# --------------------------------------------------------------------------
# scores（发音评分）
# --------------------------------------------------------------------------
def add_score(pid: str, seg_idx: int, *, score: float, accuracy: float = 0, fluency: float = 0,
              completeness: float = 0, target: str = "", transcript: str = "",
              detail: Optional[dict] = None, recording: str = "", mode: str = "repeat") -> dict:
    cur = execute(
        "INSERT INTO scores (project_id,seg_idx,score,accuracy,fluency,completeness,target,"
        "transcript,detail,recording,mode,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, seg_idx, score, accuracy, fluency, completeness, target, transcript,
         dumps(detail or {}), recording, mode, now_iso()),
    )
    return query_one("SELECT * FROM scores WHERE id=?", (cur.lastrowid,))  # type: ignore[return-value]


def list_scores(pid: str, seg_idx: Optional[int] = None, limit: int = 100) -> list[dict]:
    if seg_idx is None:
        rows = query("SELECT * FROM scores WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (pid, limit))
    else:
        rows = query("SELECT * FROM scores WHERE project_id=? AND seg_idx=? ORDER BY created_at DESC LIMIT ?",
                     (pid, seg_idx, limit))
    for r in rows:
        r["detail"] = loads(r.get("detail"), {})
    return rows


def best_scores(pid: str) -> dict:
    """每个句子的最高分，用于在句库/转写里显示掌握度。"""
    rows = query(
        "SELECT seg_idx, MAX(score) AS best, COUNT(*) AS tries FROM scores WHERE project_id=? GROUP BY seg_idx",
        (pid,),
    )
    return {int(r["seg_idx"]): {"best": r["best"], "tries": r["tries"]} for r in rows}


# --------------------------------------------------------------------------
# reviews（间隔重复队列）
# --------------------------------------------------------------------------
REVIEW_FIELDS = ("ease", "interval_days", "reps", "lapses", "due_at", "last_at",
                 "last_score", "best_score", "reason", "status", "seg_idx")


def upsert_review(pid: str, seg_idx: int, **fields: Any) -> dict:
    existing = query_one("SELECT * FROM reviews WHERE project_id=? AND seg_idx=?", (pid, seg_idx))
    if existing:
        if fields:
            update_review(existing["id"], **fields)
        return get_review(existing["id"])  # type: ignore[return-value]
    execute(
        "INSERT INTO reviews (project_id,seg_idx,ease,interval_days,reps,lapses,due_at,last_at,"
        "last_score,best_score,reason,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid, int(seg_idx),
            float(fields.get("ease", 2.5)), float(fields.get("interval_days", 0)),
            int(fields.get("reps", 0)), int(fields.get("lapses", 0)),
            fields.get("due_at") or now_iso(), fields.get("last_at") or "",
            float(fields.get("last_score", 0)), float(fields.get("best_score", 0)),
            fields.get("reason", ""), fields.get("status", "new"), now_iso(),
        ),
    )
    return query_one("SELECT * FROM reviews WHERE project_id=? AND seg_idx=?", (pid, seg_idx))  # type: ignore[return-value]


def get_review(rid: int) -> Optional[dict]:
    return query_one("SELECT * FROM reviews WHERE id=?", (rid,))


def list_reviews(pid: Optional[str] = None, status: str = "", due_before: str = "",
                 limit: int = 500) -> list[dict]:
    sql = "SELECT * FROM reviews WHERE 1=1"
    params: list[Any] = []
    if pid:
        sql += " AND project_id=?"
        params.append(pid)
    if status:
        sql += " AND status=?"
        params.append(status)
    if due_before:
        sql += " AND (due_at IS NULL OR due_at<=?)"
        params.append(due_before)
    sql += " ORDER BY due_at LIMIT ?"
    params.append(limit)
    return query(sql, params)


def update_review(rid: int, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if k in REVIEW_FIELDS}
    if not fields:
        return
    cols = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE reviews SET {cols} WHERE id=?", (*fields.values(), rid))


def delete_review(rid: int) -> None:
    execute("DELETE FROM reviews WHERE id=?", (rid,))


def review_counts(pid: Optional[str] = None) -> dict:
    """按状态统计（due 需要调用方传当前时间比较）。"""
    if pid:
        rows = query("SELECT status, COUNT(*) AS n FROM reviews WHERE project_id=? GROUP BY status", (pid,))
    else:
        rows = query("SELECT status, COUNT(*) AS n FROM reviews GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


# --------------------------------------------------------------------------
# practice_log（训练流水，统计面板的数据源）
# --------------------------------------------------------------------------
def add_practice(pid: str, seg_idx: int = -1, kind: str = "repeat", score: float = 0,
                 seconds: float = 0, day: str = "", created_at: str = "") -> None:
    execute(
        "INSERT INTO practice_log (project_id,seg_idx,kind,score,seconds,day,created_at) VALUES (?,?,?,?,?,?,?)",
        (pid, int(seg_idx), kind, float(score), float(seconds), day or (created_at or now_iso())[:10],
         created_at or now_iso()),
    )


def list_practice(pid: Optional[str] = None, days: int = 0, limit: int = 20000) -> list[dict]:
    sql = "SELECT * FROM practice_log WHERE 1=1"
    params: list[Any] = []
    if pid:
        sql += " AND project_id=?"
        params.append(pid)
    if days > 0:
        sql += " AND day>=?"
        params.append((date.today() - timedelta(days=days)).isoformat())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return query(sql, params)


# --------------------------------------------------------------------------
# pair_drills（最小对立对单词训练）
# --------------------------------------------------------------------------
def add_pair_drill(pair_id: str, word: str = "", kind: str = "say", correct: bool = False,
                   score: float = 0, expected: str = "", heard: str = "",
                   detail: Optional[dict] = None, created_at: str = "") -> int:
    ts = created_at or now_iso()
    return execute(
        "INSERT INTO pair_drills (pair_id,word,kind,correct,score,expected,heard,detail,day,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pair_id, word, kind, 1 if correct else 0, float(score), expected, heard,
         dumps(detail or {}), ts[:10], ts),
    )


def list_pair_drills(pair_id: Optional[str] = None, kind: Optional[str] = None,
                     days: int = 0, limit: int = 5000) -> list[dict]:
    sql = "SELECT * FROM pair_drills WHERE 1=1"
    params: list[Any] = []
    if pair_id:
        sql += " AND pair_id=?"
        params.append(pair_id)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    if days > 0:
        sql += " AND day>=?"
        params.append((date.today() - timedelta(days=days)).isoformat())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, params)
    for r in rows:
        r["detail"] = loads(r.get("detail"), {})
    return rows


def pair_drill_stats() -> dict:
    """按词对汇总：练了几次、对了几次、平均分。"""
    rows = query(
        "SELECT pair_id, kind, COUNT(*) AS n, SUM(correct) AS ok, AVG(score) AS avg_score"
        " FROM pair_drills GROUP BY pair_id, kind"
    )
    out: dict = {}
    for r in rows:
        out.setdefault(r["pair_id"], {})[r["kind"]] = {
            "n": r["n"], "ok": int(r["ok"] or 0), "avg_score": round(float(r["avg_score"] or 0), 1),
        }
    return out


def word_drill_stats(limit: int = 5000) -> list[dict]:
    """按 (词对, 词) 汇总练习历史 —— 用来把「练砸过的词」补进复习队列。"""
    return query(
        "SELECT pair_id, word, kind, COUNT(*) AS n, SUM(correct) AS ok,"
        " MAX(score) AS best, AVG(score) AS avg_score, MAX(created_at) AS last_at"
        " FROM pair_drills WHERE word<>'' GROUP BY pair_id, word, kind"
        " ORDER BY last_at DESC LIMIT ?",
        (limit,),
    )


# --------------------------------------------------------------------------
# word_reviews（单词卡复习队列）
# --------------------------------------------------------------------------
WORD_REVIEW_FIELDS = ("pair_id", "word", "ease", "interval_days", "reps", "lapses",
                      "due_at", "last_at", "last_score", "best_score", "reason", "status")


def upsert_word_review(pair_id: str, word: str, **fields: Any) -> dict:
    key = (pair_id, str(word).strip().lower())
    existing = query_one("SELECT * FROM word_reviews WHERE pair_id=? AND word=?", key)
    if existing:
        if fields:
            update_word_review(existing["id"], **fields)
        return get_word_review(existing["id"])  # type: ignore[return-value]
    execute(
        "INSERT INTO word_reviews (pair_id,word,ease,interval_days,reps,lapses,due_at,last_at,"
        "last_score,best_score,reason,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            key[0], key[1],
            float(fields.get("ease", 2.5)), float(fields.get("interval_days", 0)),
            int(fields.get("reps", 0)), int(fields.get("lapses", 0)),
            fields.get("due_at") or now_iso(), fields.get("last_at") or "",
            float(fields.get("last_score", 0)), float(fields.get("best_score", 0)),
            fields.get("reason", ""), fields.get("status", "new"), now_iso(),
        ),
    )
    return query_one("SELECT * FROM word_reviews WHERE pair_id=? AND word=?", key)  # type: ignore[return-value]


def get_word_review(wrid: int) -> Optional[dict]:
    return query_one("SELECT * FROM word_reviews WHERE id=?", (wrid,))


def find_word_review(pair_id: str, word: str) -> Optional[dict]:
    return query_one("SELECT * FROM word_reviews WHERE pair_id=? AND word=?",
                     (pair_id, str(word).strip().lower()))


def list_word_reviews(status: str = "", due_before: str = "", limit: int = 500) -> list[dict]:
    sql = "SELECT * FROM word_reviews WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if due_before:
        sql += " AND (due_at IS NULL OR due_at<=?)"
        params.append(due_before)
    sql += " ORDER BY due_at LIMIT ?"
    params.append(limit)
    return query(sql, params)


def update_word_review(wrid: int, **fields: Any) -> None:
    fields = {k: v for k, v in fields.items() if k in WORD_REVIEW_FIELDS}
    if not fields:
        return
    cols = ",".join(f"{k}=?" for k in fields)
    execute(f"UPDATE word_reviews SET {cols} WHERE id=?", (*fields.values(), wrid))


def delete_word_review(wrid: int) -> None:
    execute("DELETE FROM word_reviews WHERE id=?", (wrid,))


def word_review_counts() -> dict:
    rows = query("SELECT status, COUNT(*) AS n FROM word_reviews GROUP BY status")
    return {r["status"]: r["n"] for r in rows}
