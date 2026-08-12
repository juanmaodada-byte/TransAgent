# TransAgent 任务分派（D1）

> 每个模块独立文件夹，只通过 `transagent/interface.py` 的数据类通信。
> 修改接口前通知全员。D3 起接口冻结，只能新增不能删除。

---

## Vibe Coder A — 翻译核心

**目录**：`transagent/backend/core/`

| 文件 | 状态 | 关键任务 |
|------|------|---------|
| `llm_client.py` | ✅ 已有骨架 | 测试DeepSeek API连接；备选模型切换逻辑验证 |
| `pre_agent.py` | ✅ D3完成 | 策略/术语Prompt已调优（few-shot+JSON约束）；RAG查询按配置开关启用（`rag_verification_enabled`·默认关，成员C完成RAG后打开）；测试 `tests/test_pre_agent_d3.py`（5项全过） |
| `translate_agent.py` | ✅ D4完成 | 主译Prompt调优（双方向few-shot示例·Markdown结构保留·翻译腔规避规则收紧）；一致性预检强化（占位符双保护{NT_n}+{T_n}·术语一致性真实检测·方向感知）；串行/并行共用 `_run_consistency_check()`；修复存量 `{NT_n}` f-string bug（并行路径NameError）；测试 `tests/test_translate_agent_d4.py`（11项全过） |
| `post_agent.py` | ✅ 已有骨架 | 质检Prompt调优（D5开始）；润色效果对比 |
| `orchestrator.py` | ✅ 已有骨架 | 全流程串联（D6）；与pipeline/knowledge模块对接 |
| `degradation.py` | ✅ 已有骨架 | L0-L3降级策略完善（D8） |

**D1 立即要做**：
1. 设置环境变量 `DEEPSEEK_API_KEY`
2. 用 `llm_client.chat()` 发第一条测试请求
3. 验证 `orchestrator.py` 的 import 链路是否完整

---

## Vibe Coder B — 文档管线

**目录**：`transagent/backend/pipeline/`

| 文件 | 状态 | 关键任务 |
|------|------|---------|
| `structure_parser.py` | ✅ 已有完整实现 | 添加更多ICT白名单词汇；测试边界case（嵌套代码块、Mermaid复杂语法） |
| `chunker.py` | ✅ 已有完整实现 | 测试不同长度文档的分块效果；验证token估算精度 |
| `preprocess.py` | ✅ 已有完整实现 | 测试 docx→MD 转换质量（表格、列表、图片提取） |
| `restore.py` | ✅ 已有完整实现 | 配合 structure_parser 验证占位符还原完整度 |
| `aligner.py` | ✅ 已有完整实现 | 测试中英混排文档的句级对齐精度 |
| `exporter.py` | ✅ 已有骨架 | 完善 docx 反向重建（表格、图片嵌回、Mermaid渲染） |

**D1 立即要做**：
1. `pip install python-docx rapidfuzz`
2. 丢一篇 ICT docx 文档进 `preprocess.py`，看完整预处理链路是否跑通
3. 补充白名单字典（把你自己知道的ICT高频命令加进去）

---

## 成员 C — 知识库

**目录**：`transagent/backend/knowledge/` + `transagent/data/`

| 文件 | 状态 | 关键任务 |
|------|------|---------|
| `rag_terms.py` | ✅ 已有骨架 | 下载 bge-m3 模型；创建 ChromaDB collection；验证检索精度 |
| `tm_store.py` | ✅ 已有完整实现 | 测试 RapidFuzz 模糊匹配精度；调整阈值参数 |
| `user_prefs.py` | ✅ 已有完整实现 | （后续配合翻译流程测试） |
| `data/seed_terms.json` | ✅ ~115条ICT种子术语 | **核心工作**：从公开源补充到200+条。来源：K8s中文文档术语表、CNCF Glossary、微软语言门户 |

**D1 立即要做**：
1. `pip install chromadb sentence-transformers`
2. 下载 bge-m3 模型（首次会自动下载，约2GB）
3. 运行 `rag_terms.import_seed_terms()` 导入种子数据
4. 用几个ICT术语测试 `search_rag("rolling update", "demo_user", "Kubernetes/云原生")` 是否能命中
5. 补充种子术语到200条（CNCF Glossary → 中文译法）

---

## 成员 D — 前端 React

**目录**：`transagent/frontend/`

| 组件 | 状态 | 功能 |
|------|------|------|
| `FileUpload.tsx` | ❌ 待创建 | 拖拽/点击上传，格式检测反馈 |
| `ProgressBar.tsx` | ❌ 待创建 | 翻译阶段进度 + 预估时间（SSE接收） |
| `TranslateViewer.tsx` | ❌ 待创建 | Markdown渲染 + 代码高亮 |
| `QAPanel.tsx` | ❌ 待创建 | 质检报告展示 |
| `ExportButton.tsx` | ❌ 待创建 | 格式选择 + 下载 |
| `useTranslateSSE.ts` | ❌ 待创建 | SSE事件流接收Hook |

**D1 立即要做**：
1. `npx create-react-app transagent/frontend --template typescript`
2. `npm install react-markdown react-syntax-highlighter`
3. 先 mock 数据把所有组件布局搭出来（不依赖后端）
4. 后端 SSE 接口就绪后对接 `useTranslateSSE`

**接口契约见**：`transagent/interface.py` 底部的 `UPLOAD_RESPONSE_SCHEMA` 和 SSE 事件格式。

---

## 成员 E — 质量+演示

**D1 立即要做**：
1. 准备5篇 ICT 测试文档（覆盖场景：K8s技术博客·Docker教程·API文档·技术白皮书·混合代码+图表）
2. 用 ChatGPT/DeepSeek 直接翻译这5篇文档（一句话 prompt："把以下ICT文档翻译成中文"）
3. 记录 Prompt 基线测试结果
4. 准备PPT大纲

**测试文档建议**：
- 文档1：Kubernetes官方博客一篇（~1500词·含代码块·YAML配置·术语密集）
- 文档2：Docker入门教程节选（~2000词·命令行密集·简单术语）
- 文档3：REST API文档节选（~1000词·端点描述·JSON示例·参数表）
- 文档4：CNCF技术白皮书节选（~2500词·抽象概念·长句多）
- 文档5：云原生技术博客（~1800词·Mermaid架构图·混合术语·企业文化用语）

---

## 模块依赖关系

```
interface.py  ←── 所有人看，数据契约
    │
    ├── backend/config.py     ←── 所有人用
    │
    ├── backend/pipeline/*    ←── Vibe Coder B
    │       │
    │       └── preprocess.py → PreprocessResult → Vibe Coder A
    │
    ├── backend/knowledge/*   ←── 成员 C
    │       │
    │       ├── search_rag() ←── Vibe Coder A（译前术语提取时调用）
    │       └── search_tm()  ←── Vibe Coder A（译中翻译时调用）
    │
    ├── backend/core/*        ←── Vibe Coder A（编排者·调用 B+C）
    │       │
    │       └── orchestrator.py → 主入口
    │
    ├── backend/server.py    ←── Vibe Coder A + 成员 D 对接
    │
    └── frontend/             ←── 成员 D（通过HTTP API对接）
```

**开发顺序**：
1. D1-D2: B(管线) + C(知识库) 并行 → 产出可测试的独立模块
2. D3-D5: A(翻译核心) 对接 B+C → 3个Sub-Agent逐个调通
3. D6: 全流程联调（A+B+C → orchestrator → server）
4. D7: 内部Demo + 前端对接
