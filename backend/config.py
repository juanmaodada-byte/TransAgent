"""
TransAgent 全局配置
===================
v1.0 | 2026-08-06

所有模块通过此文件读取配置，不硬编码。
支持环境变量覆盖（比赛Demo时可直接改此文件）。
"""

import os
import json
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM API 配置"""
    primary_model: str = "deepseek-v4-flash"   # DeepSeek V4 Flash
    primary_api_key: str = ""                  # 从环境变量 DEEPSEEK_API_KEY 读取
    primary_base_url: str = "https://api.deepseek.com/v1"
    backup_model: str = "qwen-plus"            # 备选：通义千问
    backup_api_key: str = ""                   # 从环境变量 QWEN_API_KEY 读取
    backup_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    max_retries: int = 6                       # 失败重试次数（D7提高·json_mode间歇空响应需更多重试）
    retry_delay_seconds: float = 1.0           # 重试基础延迟（指数退避）
    request_timeout_seconds: int = 120         # 单次请求超时
    reasoning_effort: str = "low"              # D9.1：deepseek-v4-flash 是推理模型，默认思考烧光 max_tokens→空响应；设为 low 快3-30倍且不再空（""=不传该参数）


@dataclass
class ChunkConfig:
    """文档分块配置"""
    max_tokens_per_chunk: int = 1500           # 单chunk最大token（D7调低·术语提取json_mode长输入返回空）
    target_tokens_ratio: float = 0.8           # 达到窗口80%就开始找切分点
    llm_fallback_model: str = "deepseek-v4-flash"  # LLM兜底分块用的模型


@dataclass
class KnowledgeConfig:
    """知识库配置"""
    # RAG术语库（旧骨架字段·已由适配层委托新交付包 config，此处仅存档对齐）
    chroma_persist_dir: str = "./data/chroma"
    embedding_model: str = "BAAI/bge-m3"
    rag_collection_name: str = "ict_terms"
    rag_top_k: int = 5                         # 术语检索返回数
    rag_similarity_threshold: float = 0.70     # 语义相似度阈值（与知识库校准值对齐）

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
    term_extraction_max_chars: int = 1200       # 单次术语提取调用最大字符数（D7·实测>1500字符模型返回空·包小+并行更稳更快）
    web_search_enabled: bool = True             # 是否启用Web搜索查证
    rag_verification_enabled: bool = True       # 是否启用RAG术语库查证（D6整合后打开·适配层委托新交付包）

    # 主译
    translate_temperature: float = 0.2
    translate_max_tokens: int = 16000

    # 一致性检查
    consistency_temperature: float = 0.1
    consistency_max_tokens: int = 2000

    # 质检
    qa_temperature: float = 0.3
    qa_max_tokens: int = 8000   # D9.1：推理型模型思考耗token·max_tokens过小→空响应

    # 润色
    polish_temperature: float = 0.4
    polish_max_tokens: int = 8000  # D9.1：同上
    # 译后单窗口「译文」上限（D8.1·实测 deepseek-v4-flash 长输入空响应·与术语提取 1200 对齐）
    post_segment_max_chars: int = 1200


@dataclass
class AppConfig:
    """应用全局配置"""
    # 工作目录
    workspace_dir: str = "./workspace"
    assets_dir: str = "./workspace/assets"

    # 文件限制
    max_file_size_bytes: int = 50 * 1024 * 1024  # 50MB
    max_source_chars: int = 10000                # 提取后源文正文字符上限（D8.1宽松护栏·防长输入空响应风暴）
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


# ── LLM 服务商元数据（设置页展示用） ──

KNOWN_PROVIDERS: list[dict] = [
    {
        "id": "deepseek",
        "label": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
    {
        "id": "qwen",
        "label": "通义千问",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo"],
    },
    {
        "id": "zhipu",
        "label": "智谱 GLM",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash"],
    },
]

# 用户保存的 LLM 配置（设置页写入，重启后仍生效）
USER_LLM_SETTINGS_PATH = os.path.join("data", "user_llm.json")


def provider_of(model: str) -> str:
    """由模型名推断服务商 id。"""
    m = model.lower()
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("qwen"):
        return "qwen"
    if m.startswith("glm"):
        return "zhipu"
    return "deepseek"


def _apply_user_llm_settings(cfg: Config) -> None:
    """从 data/user_llm.json 加载用户保存的配置（覆盖环境变量）。"""
    try:
        if not os.path.exists(USER_LLM_SETTINGS_PATH):
            return
        with open(USER_LLM_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        p = data.get("primary") or {}
        b = data.get("backup") or {}
        if p.get("model"):
            cfg.llm.primary_model = p["model"]
        if p.get("api_key"):
            cfg.llm.primary_api_key = p["api_key"]
        if p.get("base_url"):
            cfg.llm.primary_base_url = p["base_url"]
        if b.get("model"):
            cfg.llm.backup_model = b["model"]
        if b.get("api_key"):
            cfg.llm.backup_api_key = b["api_key"]
        if b.get("base_url"):
            cfg.llm.backup_base_url = b["base_url"]
    except Exception as e:
        print(f"[Config] 加载用户 LLM 设置失败: {e}")


def apply_llm_settings(primary: dict, backup: dict) -> None:
    """应用用户设置的 LLM 通道（设置页 POST），并持久化到文件。

    传入的 api_key 为空时保留现有密钥（避免前端回显密钥导致泄露）。
    """
    cfg = get_config()
    llm = cfg.llm

    if primary.get("model"):
        llm.primary_model = primary["model"]
    if primary.get("api_key"):
        llm.primary_api_key = primary["api_key"]
    if primary.get("base_url"):
        llm.primary_base_url = primary["base_url"]
    if backup.get("model"):
        llm.backup_model = backup["model"]
    if backup.get("api_key"):
        llm.backup_api_key = backup["api_key"]
    if backup.get("base_url"):
        llm.backup_base_url = backup["base_url"]

    # 持久化完整生效配置（含密钥，本地文件）
    try:
        os.makedirs(os.path.dirname(USER_LLM_SETTINGS_PATH), exist_ok=True)
        with open(USER_LLM_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "primary": {
                    "model": llm.primary_model,
                    "api_key": llm.primary_api_key,
                    "base_url": llm.primary_base_url,
                },
                "backup": {
                    "model": llm.backup_model,
                    "api_key": llm.backup_api_key,
                    "base_url": llm.backup_base_url,
                },
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] 保存用户 LLM 设置失败: {e}")


def llm_settings_payload() -> dict:
    """当前 LLM 配置的脱敏视图（设置页 GET 返回）。"""
    llm = get_config().llm
    return {
        "providers": KNOWN_PROVIDERS,
        "primary": {
            "provider": provider_of(llm.primary_model),
            "model": llm.primary_model,
            "has_key": bool(llm.primary_api_key),
            "key_masked": _mask_key(llm.primary_api_key),
            "base_url": llm.primary_base_url,
        },
        "backup": {
            "provider": provider_of(llm.backup_model),
            "model": llm.backup_model,
            "has_key": bool(llm.backup_api_key),
            "key_masked": _mask_key(llm.backup_api_key),
            "base_url": llm.backup_base_url,
        },
    }


def _mask_key(key: str) -> str:
    """密钥脱敏：sk-****abcd。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}****{key[-4:]}"


# ── 单例 ──

_config: Config | None = None


def get_config() -> Config:
    """获取全局配置单例。首次调用时从环境变量加载。"""
    global _config
    if _config is None:
        _config = Config()
        _load_from_env(_config)
        _apply_user_llm_settings(_config)
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
