# TransAgent — ICT 翻译智能体编排系统

面向 ICT 领域的智能翻译系统：粘贴文本（或先用「文档转 Markdown」工具转换长文档）→ 智能体完成策略制定、术语提取与确认、逐段翻译、译后质检润色、知识沉淀，最终导出 **Markdown / Word / HTML / 双语对照**。

- 后端：FastAPI + asyncio，SSE 流式推送翻译进度
- 前端：React 19 + Vite + TypeScript，三栏项目工作区
- 数据：RAG 术语库（chromadb + bge-m3）+ 翻译记忆 TM（sqlite + rapidfuzz）
- 模型：默认 DeepSeek（deepseek-v4-flash），备选通义千问；设置页可切换

## 快速开始（本地开发）

```bash
# 后端（端口 8000）—— 需在仓库父目录下运行
export DEEPSEEK_API_KEY=sk-xxxx
uvicorn transagent.backend.server:app --reload --port 8000

# 前端（端口 5173）
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 使用。前端可用 `VITE_USE_MOCK=true` 走纯前端 Mock（无需后端）。

## 测试

```bash
cd transagent   # 仓库根目录
python -m pytest tests/
# 单个：python -m pytest tests/test_export.py -q
# 独立脚本（非 pytest）：从仓库父目录运行，如
#   cd .. && PYTHONPATH=. python transagent/tests/test_agent_framework.py
```

## 部署（Railway / Render）

1. 在部署平台把仓库 `transagent/` 关联为 Web Service（后端）。
2. 设置环境变量：`DEEPSEEK_API_KEY`、`QWEN_API_KEY`（可选）等，见 `.env.example`。
3. 后端命令：`uvicorn transagent.backend.server:app --host 0.0.0.0 --port $PORT`（部署平台通常注入 `$PORT`，覆盖 `.env` 里的 8000）。
4. 前端单独部署（构建时注入 `VITE_API_BASE_URL=https://<后端公网地址>`），或由后端托管构建产物。

## 目录

```
backend/            FastAPI 服务 + 编排器 + 技能 + 文档管线 + 知识库
frontend/           React 工作区前端
tests/              后端测试与验证脚本
interface.py        数据契约（D1 锁定：只增不改）
configs/            运行时配置（Okapi 等）
```

详见 `CLAUDE.md`（架构与开发约定）。
