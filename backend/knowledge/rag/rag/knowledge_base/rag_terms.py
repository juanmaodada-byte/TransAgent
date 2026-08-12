"""RAG 术语库:ChromaDB + bge-m3 语义检索(见 知识库设计.md §5.1 / §6.1)。

集合结构:
  document  = term(被检索的原文术语)
  metadata  = {translation, domain, confidence, action, source, user_id, timestamp}
"""
import hashlib
import time

import chromadb

from contracts import TermEntry
from . import config
from .embedder import embed, embed_batch
from .rag_aliases import clear_aliases, lookup_alias, update_aliases
from .rag_disambiguation import lookup_blocked_terms, normalize_key, seed_default_rules


def _client() -> chromadb.PersistentClient:
    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def _collection() -> chromadb.Collection:
    client = _client()
    return client.get_or_create_collection(
        name=config.TERMS_COLLECTION,
        metadata={"hnsw:space": "cosine"},   # bge-m3 推荐 normalize + 余弦
        embedding_function=None,             # 禁用内置 ONNX embedder(墙内下载会超时),统一走 bge-m3
    )


def _term_id(user_id: str, domain: str, term: str) -> str:
    # id 含 domain:同一术语在不同领域可各自成条(同词多译靠领域区分)
    return hashlib.md5(f"{user_id}::{domain}::{term}".encode("utf-8")).hexdigest()


def init_collection() -> int:
    """确保 collection 存在,返回当前词条数。幂等。"""
    return _collection().count()


def reset_collection(user_id: str = "") -> int:
    """清空 terms 集合 + 别名表(重建内置库用)。返回清空前条数。"""
    col = _collection()
    n = col.count()
    if n > 0:
        _client().delete_collection(config.TERMS_COLLECTION)  # 整体删除后重建(1.x 不认 where={} 删除)
        _collection()
    clear_aliases(user_id or None)
    seed_default_rules()   # 重建后自动铺默认消歧规则(幂等 upsert)
    return n


def search_rag(query: str, domain: str = "", user_id: str = "",
               top_k: int = 5) -> list[TermEntry]:
    """检索术语库(缩写别名确定性命中 → 语义检索)。

    1) 先查缩写/别名:如 "CPU"→"Central Processing Unit (CPU)",零成本 100% 准确;
    2) 否则语义检索:相似度 >= RAG_MIN_SIMILARITY 视为命中;
    3) 全部低于阈值时返回空列表,调用方据此判定「未命中,转 Web 搜索 / LLM 兜底」
       (概念文档 §6.2 三级查证)。
    """
    if not query or not query.strip():
        return []

    col = _collection()
    if col.count() == 0:
        return []

    # ── ① 缩写/别名确定性命中(不依赖语义阈值)────────────────
    alias_hit = lookup_alias(query.strip(), domain, user_id)
    if alias_hit:
        term, d, owner = alias_hit
        got = col.get(ids=[_term_id(owner, d, term)],
                      include=["documents", "metadatas"])
        if got.get("ids"):
            meta = dict(got["metadatas"][0] or {})
            meta.update({"term": got["documents"][0], "_similarity": 1.0})
            return [_to_term_entry(meta)]

    # ── ② 语义检索 ─────────────────────────────────────────
    q = embed(query)
    where = _build_where(domain=domain, user_id=user_id)
    try:
        res = col.query(query_embeddings=[q], where=where, n_results=top_k)
    except chromadb.errors.InvalidDimensionException:
        return []

    hits = [e for e in _parse_results(res)
            if e.get("_similarity", 0.0) >= config.RAG_MIN_SIMILARITY]
    # 近义消歧:抑制已知高混淆簇的近似误报(宁可兜底,不可错译)
    blocked = lookup_blocked_terms(query)
    if blocked:
        hits = [e for e in hits if normalize_key(e.get("term", "")) not in blocked]
    # 置信度分级:近似命中降级,不再冒充高置信(触发概念文档 §6.2「<high→用户确认」)
    out = []
    for e in hits:
        te = _to_term_entry(e)
        te.confidence = _remap_confidence(te.confidence, float(e.get("_similarity", 0.0)))
        out.append(te)
    return out


def write_rag(entries: list[TermEntry], user_id: str = "") -> list[str]:
    """写入/更新术语。以 (user_id, term) 为稳定 id 做 upsert(重复导入不堆积)。"""
    if not entries:
        return []

    col = _collection()
    ids = [_term_id(user_id, e.domain, e.term) for e in entries]
    documents = [e.term for e in entries]
    metadatas = [{
        "translation": e.translation,
        "domain": e.domain,
        "confidence": e.confidence,
        "action": e.action,
        "source": e.source,
        "user_id": user_id,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    } for e in entries]
    # 关键:必须显式传 bge-m3 向量,否则 ChromaDB 会调用内置 ONNX embedder
    embeddings = embed_batch(documents)
    col.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    update_aliases(entries, user_id)   # 同步维护缩写/别名索引
    return ids


# ── 内部工具 ────────────────────────────────────────────

def _build_where(domain: str, user_id: str) -> dict | None:
    conds = []
    if domain:
        # 关键兜底:没填领域的通用术语(domain="")在任意领域查询下都能命中;
        # 填了领域的术语只在对应领域命中。领域消歧优先、通用术语兜底。
        conds.append({"$or": [
            {"domain": {"$eq": domain}},
            {"domain": {"$eq": ""}},
        ]})
    if user_id:
        conds.append({"user_id": {"$eq": user_id}})
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _parse_results(res: dict) -> list[dict]:
    """把 ChromaDB query 输出转成 [{term, ..., _similarity}]。"""
    ids = res.get("ids") or [[]]
    docs = res.get("documents") or [[]]
    dists = res.get("distances") or [[]]
    metas = res.get("metadatas") or [[]]

    out = []
    id_list, doc_list = ids[0] or [], docs[0] or []
    dist_list = dists[0] if dists[0] else []
    meta_list = metas[0] if metas[0] else []
    for i in range(len(id_list)):
        meta = dict(meta_list[i] or {}) if i < len(meta_list) else {}
        dist = float(dist_list[i]) if i < len(dist_list) else 1.0
        meta.update({
            "_id": id_list[i],
            "term": doc_list[i] if i < len(doc_list) else "",
            "_similarity": 1.0 - dist,
        })
        out.append(meta)
    return out


def _to_term_entry(e: dict) -> TermEntry:
    return TermEntry(
        term=e.get("term", ""),
        translation=e.get("translation", ""),
        domain=e.get("domain", ""),
        confidence=e.get("confidence", "medium"),
        action=e.get("action", "translate"),
        source=e.get("source", ""),
    )


_LEVEL = {"high": 3, "medium": 2, "low": 1}
_LEVEL_NAME = {3: "high", 2: "medium", 1: "low"}


def _remap_confidence(stored: str, similarity: float) -> str:
    """命中强度置信度重映射。

    动机(第一次 test 优化·置信度分级):同域近义近似命中的相似度只有
    0.73~0.79,若原样继承词库存的 high,会以高置信注入错误译文,且跳过
    概念文档 §6.2 的「confidence<high → 请求用户确认」。这里按命中强度降级:
      - ≥0.95(全称直查/近似全等) → high
      - 0.80~0.95(语义近似)       → medium
      - 0.70~0.80(近义/弱相似)     → low  ← 第一次 test 的 4 个误报全落此区间
    最终取「词库存置信度」与「命中强度」中较低的一档,不抬高词库标注的低置信。
    """
    stored_level = _LEVEL.get(stored, 2)
    if similarity >= 0.95:
        sim_level = 3
    elif similarity >= 0.80:
        sim_level = 2
    else:  # >= RAG_MIN_SIMILARITY(低于阈值已在 search_rag 过滤)
        sim_level = 1
    return _LEVEL_NAME[min(stored_level, sim_level)]
