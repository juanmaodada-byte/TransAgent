"""
配置模块（最小桩）
================
D1 测试基础设施 — 提供 pipeline 模块所需的最小配置，不依赖真实 API key 或外部服务。

此文件为测试桩，不包含生产配置。生产环境应由 backend/config.py 的真实实现覆盖。
"""
from dataclasses import dataclass, field
import os


@dataclass
class AppConfig:
    """应用配置桩"""
    workspace_dir: str = field(default_factory=lambda: os.path.join(os.getcwd(), "workspace"))
    max_file_size_bytes: int = 100 * 1024 * 1024  # 100MB


@dataclass
class ChunkConfig:
    """分块配置桩"""
    max_tokens_per_chunk: int = 30000


@dataclass
class LLMConfig:
    """LLM 配置桩"""
    primary_model: str = "deepseek-chat"
    primary_base_url: str = "https://api.deepseek.com/v1"
    primary_api_key: str = ""


@dataclass
class Config:
    """全局配置桩"""
    app: AppConfig = field(default_factory=AppConfig)
    chunk: ChunkConfig = field(default_factory=ChunkConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config():
    global _config
    _config = None
