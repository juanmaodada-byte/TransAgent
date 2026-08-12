"""
RAG 术语库 —— 薄适配器(方案A·整合 2026-08-11)
=================================================
对外保持旧骨架签名(A 端 term_skill / orchestrator / server 零改动),内部委托
成员 C 新交付包(rag/rag/knowledge_base,见 _backend.py)。契约统一转回
transagent.interface.TermEntry。

保留接口(旧签名):
    search_rag(term, user_id, domain="", top_k=None) -> list[TermEntry]
    write_rag_terms(terms) -> int
    get_term_count(user_id) -> int
    import_seed_terms(seed_file=None) -> int          [兼容保留·已弃用]

集成要点:
  - 参数顺序差异:新包 search_rag(query, domain, user_id, top_k) 与旧 (term, user_id,
    domain) 顺序互换 —— 本适配器全部用关键字参数委托,规避错位。
  - 领域归一化:_domain_map.normalize_domain 把策略自由标签 → 封闭词表。
  - 用户映射:_backend.kb_user_id 把 A 端 demo_user → 知识库 default。
  - 新包已内置别名层/近义消歧/置信度分级(search_rag 返回前按命中强度降级
    high/medium/low),A 端据此走「<high → 用户确认」。
"""
from transagent.interface import TermEntry
from ._backend import rag_terms as _rag, kb_user_id
from ._domain_map import normalize_domain


def search_rag(term, user_id, domain="", top_k=None):
    """旧签名(term, user_id, domain)。内部委托新包 search_rag(query, domain, user_id, top_k)。

    命中返回按相似度降序的 interface.TermEntry(confidence 已按命中强度重映射);
    全部低于 0.70 阈值返回空列表 → 调用方判定「未命中,转 Web 搜索 / LLM 兜底」。
    """
    if not term or not str(term).strip():
        return []
    uid = kb_user_id(user_id)
    hits = _rag.search_rag(
        query=str(term),
        domain=normalize_domain(domain),
        user_id=uid,
        top_k=top_k if top_k else 5,
    )
    return [_to_interface(h, uid) for h in hits]


def _to_interface(h, user_id: str) -> TermEntry:
    """contracts.TermEntry → interface.TermEntry(补 user_id/timestamp 字段)。

    新包返回的是库中实际存储的词条(如缩写 "CPU" → "Central Processing Unit (CPU)"),
    比旧骨架返回 query 本身更准确;translation/action/confidence 即库中已定译法。
    """
    return TermEntry(
        term=h.term,
        translation=h.translation,
        domain=h.domain,
        confidence=h.confidence,
        action=h.action,
        source=h.source,
        user_id=user_id,
        timestamp="",
    )


def write_rag_terms(terms) -> int:
    """旧签名(整批)。内部委托新包 write_rag(entries, user_id) -> list[str]。

    以 (user_id, domain, term) 为稳定 id 做 upsert(重复导入不堆积),并同步维护
    缩写/别名索引。领域先归一到封闭词表,保证写入与检索同值域。
    """
    if not terms:
        return 0
    user_id = kb_user_id(terms[0].user_id or "")
    normalized = [
        TermEntry(
            term=t.term,
            translation=t.translation,
            domain=normalize_domain(t.domain),
            confidence=t.confidence,
            action=t.action,
            source=t.source,
            user_id=user_id,
            timestamp=t.timestamp or "",
        )
        for t in terms
    ]
    try:
        ids = _rag.write_rag(normalized, user_id=user_id)
        return len(ids)
    except Exception as e:
        print(f"[RAG] write_rag_terms failed: {e}")
        return 0


def get_term_count(user_id: str) -> int:
    """某用户术语库总量(新包无 per-user 计数,适配层薄封装 ChromaDB 过滤计数)。"""
    try:
        col = _rag._collection()
        if col.count() == 0:
            return 0
        got = col.get(where={"user_id": kb_user_id(user_id)})
        return len(got.get("ids", [])) if got else 0
    except Exception as e:
        print(f"[RAG] get_term_count failed: {e}")
        return 0


def import_seed_terms(seed_file=None) -> int:
    """[兼容保留·已弃用] 旧种子导入。内置库已由官方 200 + 扩库 10 = 213 条随包交付,
    无需再用。如需追加术语用 import_glossary.py 或 write_rag_terms()。"""
    raise NotImplementedError(
        "import_seed_terms 已弃用:内置库(官方 200 + 扩库 10 = 213 条)已随包交付。"
        "如需追加术语请用 knowledge_base/import_glossary.py 或 write_rag_terms()。"
    )
