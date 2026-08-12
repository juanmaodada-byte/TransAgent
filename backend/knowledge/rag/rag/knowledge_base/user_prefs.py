"""用户偏好读写(SQLite,与 TM 共用 tm.db)。"""
import sqlite3
import time

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
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id         TEXT NOT NULL,
            preference_type TEXT NOT NULL,
            value           TEXT NOT NULL,
            weight          REAL DEFAULT 1.0,
            timestamp       TEXT,
            PRIMARY KEY (user_id, preference_type)
        );
        """)


def load_prefs(user_id: str) -> dict:
    """加载用户偏好,返回 {preference_type: value}。"""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT preference_type, value FROM user_prefs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {r["preference_type"]: r["value"] for r in rows}


def save_prefs(user_id: str, prefs: dict) -> None:
    """批量保存用户偏好(upsert)。"""
    init_db()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _conn() as conn:
        for key, value in prefs.items():
            conn.execute(
                """
                INSERT INTO user_prefs (user_id, preference_type, value, timestamp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, preference_type) DO UPDATE SET
                    value = excluded.value,
                    timestamp = excluded.timestamp
                """,
                (user_id, key, str(value), ts),
            )
