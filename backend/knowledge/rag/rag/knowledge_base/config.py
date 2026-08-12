"""知识库模块集中配置(阈值在此调优,无需改业务代码)。"""
import os
from pathlib import Path

# ── 路径 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "knowledge"
CHROMA_DIR = DATA_DIR / "chroma"
TM_DB_PATH = DATA_DIR / "tm.db"
RAG_ALIAS_DB_PATH = DATA_DIR / "rag_aliases.db"   # 缩写/别名解析(确定性精确匹配)
SEED_JSON_PATH = Path(__file__).resolve().parent / "seed_terms.json"

# ── Embedding ─────────────────────────────────────────
EMBED_MODEL_NAME = "BAAI/bge-m3"            # 多语言 100+,约 2.2GB(见 知识库设计.md §8)
EMBED_MODEL_LOCAL_DIR = PROJECT_ROOT / "models" / "bge-m3"  # 优先用 ModelScope 下载的本地模型
# 中国网络:HuggingFace 直连/镜像不稳定,优先 ModelScope 本地模型;此处仅作备选
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 关键:huggingface 新版默认走 Xet 传输协议(CAS 域名在墙内不可达,导致大文件卡死),
# 强制禁用,回退经典 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ── ChromaDB ──────────────────────────────────────────
TERMS_COLLECTION = "terms"

# ── 检索阈值(知识库设计.md §6;D10 用召回测试校准)────────
RAG_MIN_SIMILARITY = 0.70   # 低于此相似度 -> search_rag 返回空,判定"未命中"转 Web 搜索
TM_MIN_SIMILARITY = 0.85    # 翻译记忆命中阈值(概念文档 §6.3 已定)
TM_QUALITY_THRESHOLD = 8.5  # 质检评分门槛,>= 此分才写入 TM

DEFAULT_USER = "default"
