# TransAgent 前端

ICT 翻译智能体编排系统的 React 前端。D1-D6 已完成。

## 启动

```bash
cd transagent/frontend
npm install
npm run dev
```

开发服务器默认端口 `5173`，浏览器打开 `http://localhost:5173`。

## 技术栈

- React 19 + TypeScript + Vite 8
- react-markdown + remark-gfm（Markdown 渲染）
- react-syntax-highlighter / Prism（代码高亮，按需注册 12 种语言）
- Fetch ReadableStream 解析 SSE（POST 流式）

## 运行模式

`.env` 文件控制对接方式：

```env
# Mock 模式：脱离后端独立运行（开发/演示用）
VITE_USE_MOCK=true|false

# 后端 API 地址
VITE_API_BASE_URL=http://localhost:8000
```

- **Mock 模式**（`VITE_USE_MOCK=true`）：使用模拟 SSE，可完整演示翻译流程，无需后端。
- **真实模式**（`VITE_USE_MOCK=false`）：对接 FastAPI 后端。

## 组件结构

```
src/
├── App.tsx                   # 路由（/ 和 /translate/:sessionId）
├── types/index.ts            # TS类型（镜像后端 interface.py）
├── api/
│   ├── client.ts             # 真实 API 封装（upload/confirmTerms/export/evolution/health）
│   └── mock.ts               # Mock 客户端（VITE_USE_MOCK=true 时启用）
├── hooks/
│   ├── useTranslateSSE.ts    # SSE 流式消费 Hook（真实）
│   └── useMockTranslate.ts   # Mock SSE Hook（模拟 10 步流程）
└── components/
    ├── FileUpload/           # D2 拖拽/点击上传 + 格式检测反馈
    ├── PasteInput/           # D7 粘贴原文输入（自动格式检测 + 字符统计）
    ├── ProgressBar/          # D3 10步翻译进度 + 耗时 + SSE连接状态
    ├── TranslateViewer/      # D4 Markdown渲染 + Prism代码高亮
    ├── QAPanel/              # D5 质检报告面板（总分/维度/问题列表）
    ├── ExportButton/         # D5 导出格式下拉 + 下载
    ├── TermConfirmCard/      # 术语确认（低置信度术语确认/修改/不译）
    └── Layout/               # 全局布局
```

## 术语确认闭环

翻译遇到低置信度术语时会**暂停**，等待用户确认后再继续：

```
SSE terms_pending 事件 → TermConfirmCard 展示待确认术语
→ 用户确认/修改/设为不译 → POST /api/confirm_terms
→ 后端唤醒暂停的翻译任务 → 翻译继续
```

- 后端断点：`orchestrator._step_terminology_confirm`（支持 async 回调）
- 待确认术语在 `pre_agent` 术语提取时由 LLM 判定（把握不足的译法）
- 确认超时（120s）自动接受，避免前端断线导致翻译挂起
- Mock 模式：确认操作直接清除（不调真实 API）

## 开发进度

| 日 | 内容 | 状态 |
|----|------|------|
| D1 | React脚手架、路由、类型定义、API层 | ✅ |
| D2 | FileUpload 组件（拖拽/点击上传、格式检测） | ✅ |
| D3 | ProgressBar + useTranslateSSE（SSE流式） | ✅ |
| D4 | TranslateViewer（Markdown + 代码高亮） | ✅ |
| D5 | QAPanel + ExportButton | ✅ |
| D6 | 对接真实后端 API + SSE 流式联调 | ✅ |
| D7 | PasteInput（粘贴原文输入）+ UI 打磨 | ✅ |

## 后端启动（真实模式）

```bash
# 从项目根目录（transagent 的父目录）启动
cd "d:/Side Projects/Developing/TransAgent"
python -m uvicorn transagent.backend.server:app --host 0.0.0.0 --port 8000
```

> 注意：后端使用绝对导入 `transagent.*`，必须从根目录启动。
> 真实翻译需要设置环境变量 `DEEPSEEK_API_KEY`；未设置时走降级路径（返回原文 + 基础评分）。
