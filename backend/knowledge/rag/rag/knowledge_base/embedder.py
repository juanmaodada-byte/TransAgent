"""bge-m3 embedding 封装:单例加载,批量预嵌入 + 单条查询嵌入。

首次调用会触发模型下载(HF_ENDPOINT 已由 config 设为 hf-mirror 镜像)。
"""
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    # 优先加载本地 ModelScope 下载的模型(墙内最稳);否则走 HF(镜像)
    if config.EMBED_MODEL_LOCAL_DIR.exists():
        return SentenceTransformer(str(config.EMBED_MODEL_LOCAL_DIR),
                                   local_files_only=True)
    return SentenceTransformer(config.EMBED_MODEL_NAME)


def embed(text: str) -> list[float]:
    """单条文本 -> 归一化向量(余弦检索用)。运行期每次查询只调用一次。"""
    vec = _get_model().encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """批量文本 -> 归一化向量列表(种子导入时用,比逐条快很多)。"""
    vecs = _get_model().encode(texts, normalize_embeddings=True, batch_size=batch_size)
    return [v.tolist() for v in vecs]
