"""知识库适配层内部加载器 —— 把成员 C 交付包(rag/rag)挂到 sys.path 后导入。

新交付包是独立包:`knowledge_base/` 内使用顶层 `from contracts import ...`
(contracts.py 位于 rag/rag 根),不能直接按
`transagent.backend.knowledge.rag.rag.knowledge_base` 导入——需要先将其根目录
加入 sys.path。本模块集中做一次挂载,三个适配器(rag_terms/tm_store/user_prefs)
复用同一批委托对象。交付包代码保持原封不动,契约差异全部收口在适配层。
"""
import sys
from pathlib import Path

# 交付包根:backend/knowledge/rag/rag/
_RAG_PKG_ROOT = Path(__file__).resolve().parent / "rag" / "rag"
if str(_RAG_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_RAG_PKG_ROOT))

from knowledge_base import rag_terms, tm_store, user_prefs  # noqa: E402  解析到 rag/rag/knowledge_base
from knowledge_base import config as kb_config               # noqa: E402

__all__ = ["rag_terms", "tm_store", "user_prefs", "kb_config", "kb_user_id"]


def kb_user_id(user_id: str) -> str:
    """A 端 user_id → 知识库 MVP 单用户(default)。

    内置库 213 条数据存于 user_id="default"(官方术语表导入默认);A 端默认
    demo_user。演示阶段统一映射到 default,形成单一积累空间(「越用越好」叙事);
    后续多用户时放开此映射即可。
    """
    if user_id and user_id != "demo_user":
        return user_id
    return kb_config.DEFAULT_USER
