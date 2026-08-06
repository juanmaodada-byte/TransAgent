"""
翻译记忆TM
==========
成员 C | v1.0 | 2026-08-06

职责：基于SQLite + RapidFuzz的传统CAT翻译记忆。
      模板化ICT文本字符串模糊匹配，精准且零成本。

使用：
    from transagent.backend.knowledge.tm_store import search_tm, write_tm_entries
    refs = search_tm("Run the following command:", user_id="user_001")
"""

import sqlite3
import os
import json
from transagent.interface import TMEntry
from transagent.backend.config import get_config


def _get_conn() -> sqlite3.Connection:
    """获取SQLite连接（延迟初始化 + 自动建表）"""
    cfg = get_config().knowledge
    db_dir = os.path.dirname(cfg.tm_db_path)
    os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(cfg.tm_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS translation_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_seg TEXT NOT NULL,
            target_seg TEXT NOT NULL,
            quality_score REAL DEFAULT 0.0,
            domain TEXT DEFAULT '',
            user_id TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            UNIQUE(source_seg, user_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tm_user ON translation_memory(user_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tm_domain ON translation_memory(domain)
    """)
    conn.commit()
    return conn


def search_tm(source_text: str, user_id: str,
              threshold: float | None = None,
              top_k: int | None = None) -> list[TMEntry]:
    """
    翻译记忆模糊搜索。

    Args:
        source_text: 待匹配的源文本（可以是全文或句段）
        user_id: 用户ID（个人化过滤）
        threshold: 相似度阈值（0-1），默认从配置读取
        top_k: 返回数，默认从配置读取

    Returns:
        匹配的TMEntry列表，按相似度降序
    """
    cfg = get_config().knowledge
    if threshold is None:
        threshold = cfg.tm_similarity_threshold
    if top_k is None:
        top_k = cfg.tm_top_k

    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        raise ImportError("rapidfuzz 未安装。pip install rapidfuzz")

    conn = _get_conn()
    rows = conn.execute(
        "SELECT source_seg, target_seg, quality_score, domain, timestamp "
        "FROM translation_memory WHERE user_id = ?",
        (user_id,)
    ).fetchall()

    if not rows:
        return []

    # 构建候选句段列表
    candidates = {r[0]: r for r in rows}

    # 如果source_text很长（全文），先按句号分割再匹配
    # 如果较短（单句），直接匹配
    segments = _split_sentences(source_text) if len(source_text) > 500 else [source_text]

    results: dict[str, TMEntry] = {}  # 用source_seg去重

    for seg in segments:
        if len(seg.strip()) < 10:  # 跳过太短的片段
            continue

        matches = process.extract(
            seg, list(candidates.keys()),
            scorer=fuzz.token_sort_ratio,
            limit=top_k,
            score_cutoff=int(threshold * 100),
        )

        for matched_text, score, _ in matches:
            similarity = score / 100.0
            row = candidates[matched_text]
            if matched_text not in results or similarity > results[matched_text].similarity:
                results[matched_text] = TMEntry(
                    source_seg=row[0],
                    target_seg=row[1],
                    quality_score=row[2],
                    similarity=similarity,
                    domain=row[3],
                    user_id=user_id,
                    timestamp=row[4],
                )

    return sorted(results.values(), key=lambda x: x.similarity, reverse=True)[:top_k]


def write_tm_entries(entries: list[TMEntry]) -> int:
    """
    批量写入TM句对。

    Args:
        entries: 待写入的TMEntry列表（quality_score应≥8.5）

    Returns:
        成功写入的条数
    """
    if not entries:
        return 0

    conn = _get_conn()
    count = 0

    for e in entries:
        try:
            conn.execute(
                """INSERT OR REPLACE INTO translation_memory
                   (source_seg, target_seg, quality_score, domain, user_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (e.source_seg, e.target_seg, e.quality_score, e.domain, e.user_id)
            )
            count += 1
        except Exception as exc:
            print(f"[TM] write failed for '{e.source_seg[:50]}...': {exc}")

    conn.commit()
    return count


def get_tm_count(user_id: str) -> int:
    """获取某用户的TM总量"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM translation_memory WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    return row[0] if row else 0


def _split_sentences(text: str) -> list[str]:
    """简单句级分割（确定性NLP，非LLM）"""
    import re
    # 按句号、分号、换行分段
    segments = re.split(r'(?<=[.;!?])\s+', text)
    # 合并太短的段
    merged = []
    buffer = ""
    for seg in segments:
        if len(buffer) + len(seg) < 30:
            buffer += " " + seg if buffer else seg
        else:
            if buffer:
                merged.append(buffer.strip())
            buffer = seg
    if buffer:
        merged.append(buffer.strip())
    return merged
