"""翻译记忆 TM:SQLite + RapidFuzz 模糊匹配(见 知识库设计.md §5.2 / §6.2)。

设计:译前主译对全部 chunk 源文做全文模糊搜索,相似度 >= TM_MIN_SIMILARITY 的
句段作为参考注入 prompt;译后质检 >= 8.5 分的句对由 write_tm 写入。
"""
import sqlite3
import time

from rapidfuzz import fuzz, process

from contracts import TMEntry
from . import config


def _conn() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.TM_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表,幂等。"""
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tm_entries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source_seg    TEXT NOT NULL,
            target_seg    TEXT NOT NULL,
            quality_score REAL DEFAULT 0.0,
            domain        TEXT DEFAULT '',
            user_id       TEXT DEFAULT '',
            timestamp     TEXT,
            UNIQUE(user_id, source_seg)
        );
        CREATE INDEX IF NOT EXISTS idx_tm_user ON tm_entries(user_id);
        """)


def search_tm(source_seg: str, threshold: float = config.TM_MIN_SIMILARITY,
              top_k: int = 5, user_id: str = "") -> list[TMEntry]:
    """RapidFuzz 全文模糊匹配,返回按相似度降序、>= threshold 的命中列表。"""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT source_seg, target_seg, quality_score FROM tm_entries WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    if not rows:
        return []

    choices = [r["source_seg"] for r in rows]
    matched = process.extract(source_seg, choices, scorer=fuzz.WRatio, limit=top_k)

    out: list[TMEntry] = []
    for text, score, idx in matched:
        sim = score / 100.0
        if sim < threshold:
            continue
        row = rows[idx]
        out.append(TMEntry(
            source_seg=row["source_seg"],
            target_seg=row["target_seg"],
            quality_score=row["quality_score"],
            similarity=sim,
        ))
    return out


def write_tm(entry: TMEntry, user_id: str = "") -> int:
    """写入/更新一条翻译记忆。同 (user_id, source_seg) 走 UPDATE(不堆积)。"""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tm_entries (source_seg, target_seg, quality_score, user_id, timestamp)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source_seg) DO UPDATE SET
                target_seg = excluded.target_seg,
                quality_score = excluded.quality_score
            """,
            (entry.source_seg, entry.target_seg, entry.quality_score, user_id,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        return cur.lastrowid or 0
