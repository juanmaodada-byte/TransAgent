"""知识库模块 —— 对外导出 6 个接口(见 知识库设计.md §4)。

用法:
    from knowledge_base import search_rag, write_rag, search_tm, write_tm, load_prefs, save_prefs
"""
from .rag_terms import init_collection, search_rag, write_rag
from .tm_store import init_db as init_tm_db
from .tm_store import search_tm, write_tm
from .user_prefs import init_db as init_prefs_db
from .user_prefs import load_prefs, save_prefs

__all__ = [
    "search_rag", "write_rag",
    "search_tm", "write_tm",
    "load_prefs", "save_prefs",
    "init_collection", "init_tm_db", "init_prefs_db",
]
