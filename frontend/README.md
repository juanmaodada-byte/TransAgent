# TransAgent 前端

## 启动

```bash
cd transagent/frontend
npm install
npm run dev
```

## 技术栈

- React 18 + TypeScript
- react-markdown + Prism (代码高亮)
- SSE 流式接收

## 组件结构

```
src/
├── App.tsx                   # 主路由
├── components/
│   ├── FileUpload.tsx        # 文件拖拽上传
│   ├── ProgressBar.tsx       # 翻译进度条（含预估时间）
│   ├── TermConfirmCard.tsx   # 术语确认卡片
│   ├── StrategyViewer.tsx    # 策略书展示
│   ├── TranslateViewer.tsx   # Markdown翻译结果渲染（代码高亮）
│   ├── QAPanel.tsx           # 质检报告面板
│   ├── ExportButton.tsx      # 格式选择+下载
│   └── EvolutionDashboard.tsx # 进化数据可视化
├── hooks/
│   └── useTranslateSSE.ts    # SSE流式接收Hook
├── api/
│   └── client.ts             # API调用封装
└── types/
    └── index.ts              # 前端TypeScript类型定义（对应interface.py）
```
