# 知识库模块(成员 C)

RAG 术语库 + 翻译记忆 TM + 用户偏好。设计依据见 [知识库设计.md](../知识库设计.md)。

## 快速开始

```bash
# 1. 安装依赖(首次)
python -m venv venv
.\venv\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU 版,避免拉 CUDA
pip install -r requirements.txt

# 2. 导入种子术语(首次会下载 bge-m3 ~2.2GB,墙内走 hf-mirror)
python -m knowledge_base.import_seed

# 3. 召回率测试
python tests/eval_recall.py                          # 官方词库测试
python tests/eval_recall_article.py                  # 真实文档测试(ICT会议论文)
python tests/eval_recall_book.py                     # 真实书籍测试(UNIX网络编程前5000字)
```

## 6 个接口

```python
from knowledge_base import search_rag, write_rag, search_tm, write_tm, load_prefs, save_prefs

# RAG 术语库
search_rag("rolling update", domain="kubernetes")   # -> [TermEntry]; 空列表=未命中(转Web)
write_rag([TermEntry(term="...", translation="...", domain="kubernetes")])

# 翻译记忆
search_tm("Run the following command:")             # -> [TMEntry] (>=85% 命中)
write_tm(TMEntry(source_seg="...", target_seg="...", quality_score=9.0))

# 用户偏好
save_prefs("default", {"style": "技术文档", "direct_or_free": "直译为主"})
load_prefs("default")
```

## 数据位置

- `data/knowledge/chroma/` — ChromaDB 术语库持久化
- `data/knowledge/tm.db` — TM + 用户偏好(SQLite)

## 阈值调优

集中在 `knowledge_base/config.py`:`RAG_MIN_SIMILARITY`(命中阈值)、`TM_MIN_SIMILARITY`、`TM_QUALITY_THRESHOLD`。用 `tests/eval_recall.py` 的阈值敏感性输出校准。
