"""
RAG术语库
=========
成员 C | v1.0 | 2026-08-06

职责：基于ChromaDB + bge-m3的ICT术语语义检索。
      跨项目积累个人术语资产，支持按user_id + domain过滤。

使用：
    from transagent.backend.knowledge.rag_terms import search_rag, write_rag_terms
    results = search_rag("rolling update", user_id="user_001", domain="Kubernetes/云原生")
"""

import json
import os
from transagent.interface import TermEntry, Confidence, TermSource
from transagent.backend.config import get_config


# ── ChromaDB 客户端（延迟初始化）──
_chroma_client = None
_collection = None


def _get_collection():
    """获取或创建ChromaDB collection（延迟初始化）"""
    global _chroma_client, _collection
    if _collection is None:
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError("chromadb 未安装。pip install chromadb")

        cfg = get_config().knowledge
        os.makedirs(cfg.chroma_persist_dir, exist_ok=True)

        _chroma_client = chromadb.PersistentClient(
            path=cfg.chroma_persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _chroma_client.get_or_create_collection(
            name=cfg.rag_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _get_embedding_fn():
    """获取embedding函数（延迟加载bge-m3模型）"""
    cfg = get_config().knowledge
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(cfg.embedding_model)
        return lambda texts: model.encode(list(texts), normalize_embeddings=True).tolist()
    except ImportError:
        raise ImportError("sentence-transformers 未安装。pip install sentence-transformers")


def search_rag(term: str, user_id: str, domain: str = "",
               top_k: int | None = None) -> list[TermEntry]:
    """
    语义检索术语库。

    Args:
        term: 要查询的术语
        user_id: 用户ID（个人化过滤）
        domain: ICT子领域标签（可选·用于消歧过滤）
        top_k: 返回结果数，默认从配置读取

    Returns:
        匹配的TermEntry列表，按语义相似度降序
    """
    cfg = get_config().knowledge
    if top_k is None:
        top_k = cfg.rag_top_k

    collection = _get_collection()
    embed_fn = _get_embedding_fn()

    # 构建查询过滤条件
    where_filter = {"user_id": user_id}
    if domain:
        where_filter["domain"] = domain

    try:
        query_embedding = embed_fn([term])
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where_filter,
            include=["metadatas", "documents", "distances"],
        )

        entries = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                metadata = results["metadatas"][0][i] if results["metadatas"] else {}
                distance = results["distances"][0][i] if results["distances"] else 1.0
                similarity = 1.0 - distance  # cosine distance → similarity

                if similarity >= cfg.rag_similarity_threshold:
                    entries.append(TermEntry(
                        term=term,
                        translation=metadata.get("translation", ""),
                        domain=metadata.get("domain", ""),
                        confidence=Confidence.HIGH.value,
                        action=metadata.get("action", "translate"),
                        source=TermSource.RAG_HIT.value,
                        user_id=user_id,
                        timestamp=metadata.get("timestamp", ""),
                    ))
        return entries
    except Exception as e:
        # knowledge层异常不中断主流程
        print(f"[RAG] search_rag failed: {e}")
        return []


def write_rag_terms(terms: list[TermEntry]) -> int:
    """
    批量写入术语到RAG术语库。

    Args:
        terms: 待写入的TermEntry列表

    Returns:
        成功写入的条数
    """
    if not terms:
        return 0

    cfg = get_config().knowledge
    collection = _get_collection()
    embed_fn = _get_embedding_fn()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    embeddings: list = []

    for i, t in enumerate(terms):
        term_id = f"{t.user_id}_{t.term.replace(' ', '_')}_{i}"
        ids.append(term_id)
        documents.append(t.term)
        metadatas.append({
            "translation": t.translation,
            "domain": t.domain,
            "confidence": t.confidence,
            "action": t.action,
            "source": t.source,
            "user_id": t.user_id,
            "timestamp": t.timestamp,
        })

    try:
        embeddings = embed_fn(documents)
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(ids)
    except Exception as e:
        print(f"[RAG] write_rag_terms failed: {e}")
        return 0


def import_seed_terms(seed_file: str | None = None) -> int:
    """
    导入ICT种子术语。首次使用时调用。

    Args:
        seed_file: 种子术语JSON文件路径，默认从配置读取

    Returns:
        导入条数
    """
    cfg = get_config().knowledge
    if seed_file is None:
        seed_file = cfg.seed_terms_path

    if not os.path.exists(seed_file):
        print(f"[RAG] 种子文件不存在: {seed_file}")
        return 0

    with open(seed_file, "r", encoding="utf-8") as f:
        seed_data = json.load(f)

    terms = []
    for item in seed_data:
        terms.append(TermEntry(
            term=item.get("term", ""),
            translation=item.get("translation", ""),
            domain=item.get("domain", ""),
            confidence=Confidence.HIGH.value,
            action=item.get("action", "translate"),
            source=TermSource.WHITELIST.value,
            user_id="__seed__",  # 种子数据用特殊user_id，所有用户继承
            timestamp=item.get("timestamp", ""),
        ))

    return write_rag_terms(terms)


def get_term_count(user_id: str) -> int:
    """获取某用户的术语库总量"""
    try:
        collection = _get_collection()
        result = collection.get(where={"user_id": user_id})
        return len(result["ids"]) if result and result["ids"] else 0
    except Exception:
        return 0
