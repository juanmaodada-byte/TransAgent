"""
翻译记忆 TM —— 薄适配器(方案A·整合 2026-08-11)
=================================================
对外保持旧骨架签名(A 端 orchestrator / server 零改动),内部委托成员 C 新交付包
(rag/rag/knowledge_base,见 _backend.py)。契约转回 transagent.interface.TMEntry。

保留接口(旧签名):
    search_tm(source_text, user_id, threshold=None, top_k=None) -> list[TMEntry]
    write_tm_entries(entries) -> int
    get_tm_count(user_id) -> int

集成要点:
  - 参数顺序差异:新包 search_tm(source_seg, threshold, top_k, user_id) 与旧
    (source_text, user_id, threshold, top_k) 顺序不同 —— 全部用关键字参数委托。
  - 新包 contracts.TMEntry 无 domain/user_id 字段 → 适配层补 user_id,domain 置空
    (TM 维度损失见 整合评估报告 G7,适配层按写入批次可补)。
"""
from transagent.interface import TMEntry
from ._backend import tm_store as _tm, kb_config, kb_user_id


def search_tm(source_text, user_id, threshold=None, top_k=None):
    """旧签名(source_text, user_id)。内部委托新包 search_tm(source_seg, threshold, top_k, user_id)。

    返回相似度 >= 阈值(默认 0.85)的句对,按相似度降序;命中行填充 similarity。
    """
    if not source_text:
        return []
    uid = kb_user_id(user_id)
    hits = _tm.search_tm(
        source_seg=str(source_text),
        threshold=threshold if threshold is not None else kb_config.TM_MIN_SIMILARITY,
        top_k=top_k if top_k else 10,
        user_id=uid,
    )
    return [
        TMEntry(
            source_seg=h.source_seg,
            target_seg=h.target_seg,
            quality_score=h.quality_score,
            similarity=h.similarity,
            domain="",
            user_id=uid,
        )
        for h in hits
    ]


def write_tm_entries(entries) -> int:
    """旧签名(整批)。内部逐条委托新包 write_tm(entry, user_id)。

    同 (user_id, source_seg) 走 UPDATE 不堆积(新包 ON CONFLICT upsert)。
    """
    count = 0
    for e in entries:
        try:
            rowid = _tm.write_tm(e, user_id=kb_user_id(e.user_id))
            if rowid:
                count += 1
        except Exception as ex:
            print(f"[TM] write failed for '{str(e.source_seg)[:50]}...': {ex}")
    return count


def get_tm_count(user_id: str) -> int:
    """某用户 TM 总量(新包无计数接口,适配层直查新包 SQLite tm_entries 表)。"""
    import sqlite3
    try:
        con = sqlite3.connect(str(kb_config.TM_DB_PATH))
        row = con.execute(
            "SELECT COUNT(*) FROM tm_entries WHERE user_id = ?",
            (kb_user_id(user_id),),
        ).fetchone()
        con.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"[TM] get_tm_count failed: {e}")
        return 0
