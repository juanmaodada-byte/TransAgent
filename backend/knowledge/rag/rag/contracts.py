"""共享数据契约 —— 与 开发计划.md §3「统一数据契约」一致,建议 A/B/C 三方共用。"""
from dataclasses import dataclass


@dataclass
class TermEntry:
    term: str
    translation: str
    domain: str = ""
    confidence: str = "medium"      # "high" | "medium" | "low"
    action: str = "translate"       # "translate" | "notranslate"
    source: str = ""                # "RAG命中" | "Web搜索" | "LLM生成" | "用户确认" | "种子数据"


@dataclass
class TMEntry:
    source_seg: str
    target_seg: str
    quality_score: float = 0.0
    similarity: float = 0.0         # 仅 search_tm 返回时填充;write_tm 忽略
