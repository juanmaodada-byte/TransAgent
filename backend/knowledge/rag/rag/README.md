# RAG 知识库模块(成员 C · TransAgent)

ICT 翻译智能体编排系统的知识库模块打包交付。核心能力:**RAG 术语库**(bge-m3 + ChromaDB)语义检索 + 缩写别名确定性命中 + 翻译记忆 TM(SQLite + RapidFuzz)+ 用户偏好。

> 打包日期:2026-08-11 | 库规模:**213 条**(官方 200 拆缝合怪词条 → 203 + 扩库 10)
> 本包**已含构建好的运行数据**(`data/knowledge/`),复制到任意机器安装依赖后即可开箱即用。

---

## 一、目录结构

```
rag/
├── contracts.py              # 共享数据契约(D1 锁定,TermEntry/TMEntry)
├── requirements.txt          # 依赖清单(注意 torch 需 CPU 版另装)
├── 知识库设计.md              # 设计文档(v0.3)
├── 知识库测试报告.md          # 第一次官方库召回测试(2026-08-09)
├── 测试优化报告.md            # 三次测试 + 九轮优化日志(至 2026-08-11)
├── ICT analysis and applicantions(1)-test（1）.md  # 第二次真实文档测试报告
├── ICT_Terms_200.xlsx        # 官方种子术语(200 条,来源官方渠道)
├── ICT_Terms_ext.csv         # 扩库术语(10 条,网络/AI/云/合规等)
│
├── knowledge_base/           # 核心代码
│   ├── __init__.py           # 对外导出 6 接口
│   ├── config.py             # 阈值 / 路径常量(RAG_MIN_SIMILARITY=0.70 等)
│   ├── embedder.py           # bge-m3 加载(本地 models/bge-m3,HF 镜像 + 禁 Xet)
│   ├── rag_terms.py          # ChromaDB collection + search_rag / write_rag
│   ├── rag_aliases.py        # 缩写/别名解析层(确定性精确匹配)
│   ├── rag_disambiguation.py # 近义消歧表(默认规则自动铺)
│   ├── glossary_split.py     # 缝合怪词条拆分(唯一拆分入口)
│   ├── import_glossary.py    # 术语表导入器(CSV/xlsx/MD 自适应)
│   ├── tm_store.py           # SQLite + RapidFuzz + search_tm / write_tm
│   ├── user_prefs.py         # SQLite + load_prefs / save_prefs
│   ├── import_seed.py        # [历史]模拟种子导入(存档)
│   └── seed_terms.json       # [历史]模拟种子(已被官方库替换,存档)
│
├── tests/                    # 回归 / 召回率测试
│   ├── eval_recall.py                # 官方词库召回(seed 旧集为默认,官方用 --file)
│   ├── eval_recall_article.py        # 真实文档(ICT 论文集全文)
│   ├── eval_recall_book.py           # 真实书籍(UNIX网络编程 前5000字)
│   ├── eval_queries*.json            # 各测试集
│   ├── test_smoke.py                 # 冒烟(秒级)
│   ├── test_learning_loop.py         # 学习层回写闭环
│   └── test_optimization.py          # 扩库/消歧/置信度优化验证
│
└── data/
    ├── unix_book_first5000.txt       # 第三次测试语料(OCR 前5000字)
    ├── unix_book_fulltext.txt        # 该书全文提取
    ├── ict_article_fulltext.txt      # 第二次测试语料(论文集全文)
    └── knowledge/                    # ★已构建运行数据(开箱即用)
        ├── chroma/                   # ChromaDB 向量库(213 条)
        ├── tm.db                     # 翻译记忆
        └── rag_aliases.db            # 缩写/别名索引 + 消歧规则
```

---

## 二、环境准备

```powershell
# 1. 创建虚拟环境并安装依赖
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt

# 2. torch 请单独装 CPU 版(requirements.txt 不包含):
.\venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. xlsx 读取需要 openpyxl(导入官方术语表/测试用):
.\venv\Scripts\pip install openpyxl

# 4. bge-m3 模型(约 2.2GB)放本地,供 embedder.py 使用:
#    将模型目录放到本包下  models/bge-m3/
#    (config.py 的 EMBED_MODEL_LOCAL_DIR 指向 PROJECT_ROOT/models/bge-m3)
```

模型可用 ModelScope / HuggingFace 镜像拉取:`BAAI/bge-m3`。若本地无模型,embedder 会回退到在线加载(HF 镜像已配置)。

---

## 三、快速验证(开箱即用)

包内 `data/knowledge/` 已含 213 条向量库,装好环境后直接跑:

```powershell
# 冒烟测试(SQLite 部分,秒级)
.\venv\Scripts\python.exe tests/test_smoke.py

# 学习层回写闭环 + 置信度分级 + 消歧(需 bge-m3)
.\venv\Scripts\python.exe tests/test_learning_loop.py

# 扩库治本验证
.\venv\Scripts\python.exe tests/test_optimization.py

# 官方词库回归(32/32 = 100%)
.\venv\Scripts\python.exe tests/eval_recall.py --file tests/eval_queries_official.json

# 真实文档回归(ICT 论文集,43/49 = 87.8%)
.\venv\Scripts\python.exe tests/eval_recall_article.py

# 真实书籍回归(UNIX网络编程 前5000字,36/36 = 100%)
.\venv\Scripts\python.exe tests/eval_recall_book.py
```

> 说明:`eval_recall.py` 默认测试集 `eval_queries.json` 是 seed 时代的旧集(kubernetes 领域),
> 对官方-only 库本就 FAIL;官方库测试正确命令是 `--file tests/eval_queries_official.json`。
> article/book 脚本运行时会自动重新生成各自的 `eval_queries_*.json`。

---

## 四、6 个对外接口(契约不变)

| 接口 | 功能 | 位置 |
|------|------|------|
| `search_rag(query, domain, user_id, top_k)` | 术语检索:别名确定性命中 → 语义(≥0.70) | knowledge_base/rag_terms.py |
| `write_rag(entries, user_id)` | 术语写入/更新((user_id, term) upsert) | knowledge_base/rag_terms.py |
| `search_tm(source, top_k)` | 翻译记忆模糊检索(≥0.85) | knowledge_base/tm_store.py |
| `write_tm(source, target, quality_score)` | 翻译记忆写入 | knowledge_base/tm_store.py |
| `load_prefs(user_id)` | 用户偏好读取 | knowledge_base/user_prefs.py |
| `save_prefs(user_id, prefs)` | 用户偏好保存 | knowledge_base/user_prefs.py |

**术语表导入(用户上传)**:
```powershell
# 追加导入(用户术语表 → 直接可用)
.\venv\Scripts\python.exe knowledge_base/import_glossary.py 你的术语表.csv --source "用户上传"

# 重建内置库(把某术语表设为内置库)
.\venv\Scripts\python.exe knowledge_base/import_glossary.py ICT_Terms_200.xlsx --rebuild --source "官方术语库"
```

---

## 五、测试成绩一览(2026-08-11)

| 测试集 | Recall@1 | MRR | 判定准确率 | 负例 |
|--------|---------|-----|-----------|------|
| 官方词库(32 条) | 100%(32/32) | 1.000 | 100% | 0/3 误报 |
| 真实文档·ICT 论文集(49 条) | 87.8%(43/49) | 0.888 | 91.8% | 0/12 误报 |
| 真实书籍·UNIX网络编程前5000字(36 条) | 100%(36/36) | 1.000 | 100% | 0/18 误报 |

> article 5 个未过项(`artificial intelligence techniques` 等)均为 0.65~0.68 语义弱改写,
> 低于 0.70 阈值返回空 → 按三级查证走 Web/LLM 兜底,属设计行为非缺陷。
> 0.70 是校准的召回/误报平衡点,不建议调整。

## 六、已知限制(详见 测试优化报告.md)

- 0.70 阈值下语义改写弱查询(0.65~0.70)走 Web/LLM 兜底
- 别名表主键 `(alias_key, user_id)`:`Handover`/`Handoff` 共享中文键 `切换` 仅存一条
- 待 A/D 端接线:学习层回写(`write_rag` 已就绪)、低置信确认 UI、上传 HTTP 链路
