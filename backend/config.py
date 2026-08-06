"""
TransAgent 全局配置
===================
v1.0 | 2026-08-06

所有模块通过此文件读取配置，不硬编码。
支持环境变量覆盖（比赛Demo时可直接改此文件）。
"""

import os
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM API 配置"""
    primary_model: str = "deepseek-chat"       # DeepSeek V4 Flash
    primary_api_key: str = ""                  # 从环境变量 DEEPSEEK_API_KEY 读取
    primary_base_url: str = "https://api.deepseek.com/v1"
    backup_model: str = "qwen-plus"            # 备选：通义千问
    backup_api_key: str = ""                   # 从环境变量 QWEN_API_KEY 读取
    backup_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    max_retries: int = 3                       # 失败重试次数
    retry_delay_seconds: float = 1.0           # 重试基础延迟（指数退避）
    request_timeout_seconds: int = 120         # 单次请求超时


@dataclass
class ChunkConfig:
    """文档分块配置"""
    max_tokens_per_chunk: int = 30000          # 单chunk最大token
    target_tokens_ratio: float = 0.8           # 达到窗口80%就开始找切分点
    llm_fallback_model: str = "deepseek-chat"  # LLM兜底分块用的模型


@dataclass
class KnowledgeConfig:
    """知识库配置"""
    # RAG术语库
    chroma_persist_dir: str = "./data/chroma"
    embedding_model: str = "BAAI/bge-m3"
    rag_collection_name: str = "ict_terms"
    rag_top_k: int = 5                         # 术语检索返回数
    rag_similarity_threshold: float = 0.75     # 语义相似度阈值

    # TM翻译记忆
    tm_db_path: str = "./data/tm.db"
    tm_similarity_threshold: float = 0.85      # RapidFuzz模糊匹配阈值
    tm_top_k: int = 10                         # TM参考返回数
    tm_min_quality_score: float = 8.5          # 写入TM的最低质检分

    # 种子数据
    seed_terms_path: str = "./data/seed_terms.json"


@dataclass
class PipelineConfig:
    """翻译管道配置"""
    # 策略制定
    strategy_temperature: float = 0.3           # 低温度=更确定性
    strategy_max_tokens: int = 1000

    # 术语提取
    term_extraction_temperature: float = 0.2
    term_extraction_max_tokens: int = 2000
    web_search_enabled: bool = True             # 是否启用Web搜索查证

    # 主译
    translate_temperature: float = 0.2
    translate_max_tokens: int = 16000

    # 一致性检查
    consistency_temperature: float = 0.1
    consistency_max_tokens: int = 2000

    # 质检
    qa_temperature: float = 0.3
    qa_max_tokens: int = 3000

    # 润色
    polish_temperature: float = 0.4
    polish_max_tokens: int = 8000


@dataclass
class AppConfig:
    """应用全局配置"""
    # 工作目录
    workspace_dir: str = "./workspace"
    assets_dir: str = "./workspace/assets"

    # 文件限制
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50MB
    supported_formats: list = field(default_factory=lambda: ["md", "docx", "txt"])

    # 服务
    host: str = "0.0.0.0"
    port: int = 8000

    # 默认语言方向（P0仅中英互译）
    source_lang: str = "en"
    target_lang: str = "zh-CN"

    # Demo模式
    demo_mode: bool = False                      # 比赛演示时开启（使用预置数据）
    demo_user_id: str = "demo_user"
    demo_rag_terms_count: int = 200              # 演示时预置术语数
    demo_tm_count: int = 500                     # 演示时预置TM数


@dataclass
class Config:
    """总配置容器"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    app: AppConfig = field(default_factory=AppConfig)


# ── 单例 ──

_config: Config | None = None


def get_config() -> Config:
    """获取全局配置单例。首次调用时从环境变量加载。"""
    global _config
    if _config is None:
        _config = Config()
        _load_from_env(_config)
    return _config


def _load_from_env(cfg: Config) -> None:
    """从环境变量覆盖配置"""
    cfg.llm.primary_api_key = os.getenv("DEEPSEEK_API_KEY", "")
    cfg.llm.backup_api_key = os.getenv("QWEN_API_KEY", "")
    if os.getenv("TRANSAGENT_DEMO_MODE", "").lower() in ("1", "true", "yes"):
        cfg.app.demo_mode = True
    if os.getenv("TRANSAGENT_WEB_SEARCH_DISABLED", "").lower() in ("1", "true", "yes"):
        cfg.pipeline.web_search_enabled = False
    port = os.getenv("TRANSAGENT_PORT", "")
    if port:
        cfg.app.port = int(port)


def reset_config() -> None:
    """重置配置（测试用）"""
    global _config
    _config = None
