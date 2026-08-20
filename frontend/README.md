# TransAgent 前端

ICT 翻译智能体编排系统的 React 前端 — **三栏项目工作区**（Codex/WorkBuddy 风格）。

## 启动

```bash
cd transagent/frontend
npm install
npm run dev
```

开发服务器：`http://localhost:5173`。打开后**自动进入工作台**（有项目进最近项目，无项目自动创建"默认项目"）。

## 运行模式

`.env` 控制：

```env
VITE_USE_MOCK=true|false     # Mock 演示 / 真实后端
VITE_API_BASE_URL=http://localhost:8000
```

- **Mock 模式**：脱离后端演示完整流程（终稿回显输入、模拟术语确认暂停）
- **真实模式**：对接 FastAPI + DeepSeek API（真实翻译，约 1-3 分钟/次）

后端启动（必须从项目根目录，因绝对导入）：

```bash
cd "d:/Side Projects/Developing/TransAgent"
DEEPSEEK_API_KEY=sk-xxx python -u -m uvicorn transagent.backend.server:app --port 8000
```

## 三栏布局

```
┌─────────┬──────────────────────────────┬──────────────┐
│ 左栏 220px│ 中栏（flex）                  │ 右栏 320px     │
│ 功能区    │ 对话流（用户/智能体消息）        │ 文件阅览区      │
│ 项目区    │ 输入区（文本+📎上传+拖拽）      │ 列表+预览+下载  │
└─────────┴──────────────────────────────┴──────────────┘
```

- **左栏**：功能区（导入/术语库/TM/偏好入口）+ 项目 CRUD（新建/切换/重命名/删除）+ Mock 指示
- **中栏**：翻译流程对话流（进度条 → 策略 → **术语确认暂停** → 初译稿 → 质检 → 终稿 → 进化），底部 Codex 式输入框（Ctrl+Enter 发送、📎 上传、整栏拖拽导入）
- **右栏**：文件按分类分组（翻译产出/报告/知识库），点击预览（Markdown 渲染/JSON），下载导出

## 目录结构

```
src/
├── App.tsx                    # 路由：/ → 自动进工作台，/projects/:id → 工作区
├── context/ProjectsContext.tsx  # 全局项目状态（localStorage 持久化）
├── types/project.ts           # 前端本地项目模型（项目/消息/文件）
├── storage/projectStore.ts    # localStorage CRUD + 防抖（ta.projects.v1 / ta.project.<id>.v1 / ta.ui.v1）
├── hooks/
│   ├── useProjects.ts         # 项目 actions
│   ├── useProjectRunner.ts    # 核心：SSE 事件 → 对话消息 + 项目文件
│   ├── useTranslateSSE.ts     # SSE 消费（+onEvent 回调）
│   └── useMockTranslate.ts    # Mock 模拟（术语确认暂停 + resume）
├── pages/
│   ├── AutoWorkspaceGate.tsx  # 首页自动进入工作台
│   └── WorkspacePage/         # 三栏壳
├── components/
│   ├── workspace/             # LeftPanel/ProjectList/CenterPanel/InputArea/
│   │                          # ConversationMessageItem/cards/RightPanel/FileList/FilePreview
│   └── （复用）FileUpload/PasteInput/ProgressBar/TranslateViewer/QAPanel/
│           ExportButton/TermConfirmCard/ErrorBoundary
└── utils/                     # download.ts / inputText.ts
```

## 术语确认闭环

翻译到术语确认处**暂停**，等待用户确认（5 分钟超时自动接受）：
- 后端：`on_terms_pending` async 回调 + asyncio.Event 暂停/唤醒
- 前端：`TermConfirmCard` 倒计时提示 → `confirmTerms` API → 恢复

## 开发进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 0 | 数据层（项目模型/localStorage/useProjects） | ✅ |
| Phase 1 | 三栏壳 + 左栏项目区 + Codex 式输入框 | ✅ |
| Phase 2 | 翻译流程接入对话流（SSE→消息/文件） | ✅ |
| Phase 3 | 右栏文件阅览区（列表/预览/下载） | ✅ |
| Phase 4 | 打磨（拖拽导入/UI记忆/响应式/清理） | ✅ |
