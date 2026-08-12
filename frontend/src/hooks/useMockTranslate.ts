/**
 * useMockTranslate Hook
 * =====================
 * 用 setTimeout 模拟 SSE 事件序列，支持前端独立开发和演示。
 * 接口与 useTranslateSSE 完全一致，在 TranslatePage 中通过
 * 环境变量 VITE_USE_MOCK=true 切换。
 */

import { useState, useRef, useCallback } from 'react';
import type {
  StepKey,
  StepState,
  ExportFormat,
  StrategyBook,
  TermEntry,
  QAResult,
  EvolutionReport,
} from '../types';
import { STEP_ORDER } from '../types';
import type { ConnectionStatus, UseTranslateSSEReturn } from './useTranslateSSE';

// ── Mock 数据 ──

const MOCK_STRATEGY: StrategyBook = {
  ict_domain: 'Kubernetes/云原生',
  domain_confidence: 'high',
  difficulty: 'medium',
  style: 'technical',
  literal_ratio: 0.6,
  target_audience: '开发者',
  rules: {
    code: 'notranslate',
    tone: 'professional',
    sentence_length: 'medium',
    voice: 'active',
  },
};

const MOCK_TERMS: TermEntry[] = [
  { term: 'pod', translation: '容器组', domain: 'Kubernetes', confidence: 'high', action: 'translate', source: 'RAG命中', user_id: 'demo', timestamp: '2026-08-07T10:00:00Z' },
  { term: 'namespace', translation: '命名空间', domain: 'Kubernetes', confidence: 'high', action: 'translate', source: 'RAG命中', user_id: 'demo', timestamp: '2026-08-07T10:00:00Z' },
  { term: 'controller', translation: '控制器', domain: 'Kubernetes', confidence: 'medium', action: 'translate', source: 'LLM生成', user_id: 'demo', timestamp: '2026-08-07T10:00:00Z' },
];

const MOCK_QA: QAResult = {
  total_score: 9.2,
  term_accuracy: 9.5,
  semantic_fidelity: 9.0,
  code_integrity: 10.0,
  fluency: 9.0,
  style_match: 8.5,
  issues: [
    { location: 'chunk_1 段落3', severity: 'minor', type: '翻译腔', description: '"在...的情况下"可简化为"当...时"' },
  ],
  summary: '翻译质量优秀，术语一致性好，代码块完整保留。仅1处轻微翻译腔。',
};

const MOCK_FINAL = `# Kubernetes Pod 概述

## 什么是 Pod

**Pod** 是 Kubernetes 中最小的可部署计算单元。一个 Pod 包含一个或多个容器，这些容器**共享存储和网络资源**。

> 核心概念：Pod 中的容器总是被调度到同一节点上协同运行，共享网络命名空间和存储卷。

## 创建 Pod

### 使用 YAML 清单

创建一个简单的 \`nginx\` Pod 示例：

\`\`\`yaml
apiVersion: v1
kind: Pod
metadata:
  name: nginx-demo
  labels:
    app: nginx
spec:
  containers:
  - name: nginx
    image: nginx:1.14.2
    ports:
    - containerPort: 80
\`\`\`

### 使用 kubectl 命令

\`\`\`bash
# 从清单文件创建
kubectl apply -f pod.yaml

# 查看 Pod 状态
kubectl get pods -o wide

# 查看详细事件
kubectl describe pod nginx-demo
\`\`\`

## 常用配置项

| 配置项 | 说明 | 必填 |
|--------|------|------|
| \`containers\` | 容器列表 | ✅ |
| \`restartPolicy\` | 重启策略（Always/OnFailure/Never） | ❌ |
| \`nodeSelector\` | 节点选择器 | ❌ |
| \`volumes\` | 存储卷定义 | ❌ |

## 检查 Pod 状态

1. 使用 \`kubectl get pods\` 查看状态
2. 使用 \`kubectl logs <pod-name>\` 查看日志
3. 使用 \`kubectl exec -it <pod-name> -- /bin/sh\` 进入容器

### 状态说明

- \`Pending\` — 等待调度
- \`Running\` — 正常运行
- \`Succeeded\` — 任务型 Pod 成功退出
- \`CrashLoopBackOff\` — 容器崩溃重启中

## 资源回收

当 Pod 不再需要时，使用以下命令删除：

\`\`\`bash
kubectl delete pod nginx-demo
\`\`\`

> 注意：由 \`Deployment\` 管理的 Pod 删除后会被自动重建。`;

const MOCK_EVOLUTION: EvolutionReport = {
  new_terms_count: 3,
  new_tm_count: 12,
  total_terms: 203,
  total_tm: 512,
  tm_reuse_rate: 0.35,
  rag_hit_rate: 0.88,
  summary: '本次新增3个术语·12条TM | 累计术语203·TM512',
};

// ── 步骤模拟序列 ──
// [delay_ms, step, state, message]
type MockStep = [number, StepKey, StepState, string];

const MOCK_PROGRESS: MockStep[] = [
  [500, 'input_detect', 'in_progress', '正在检测文件格式…'],
  [800, 'input_detect', 'completed', '格式检测完成: Markdown'],
  [400, 'input_convert', 'in_progress', '正在解析文档结构…'],
  [1000, 'input_convert', 'completed', '预处理完成: 3200 tokens | 3 chunks | 占位符 5处'],
  [600, 'pre_translate', 'in_progress', '译前Sub-Agent工作中（策略+术语）…'],
  [1500, 'pre_translate', 'completed', 'ICT子领域: Kubernetes/云原生 | 术语: 8个 (全部自动确认)'],
  [300, 'terminology_confirm', 'in_progress', '检查术语确认状态…'],
  [500, 'terminology_confirm', 'completed', '自动接受0个术语'],
  [800, 'translate', 'in_progress', '译中Sub-Agent工作中（串行·3 chunk）…'],
  [2500, 'translate', 'completed', '初译完成: 2847字符 | 一致性: 预检通过'],
  [600, 'post_translate', 'in_progress', '译后Sub-Agent工作中（质检→润色）…'],
  [1800, 'post_translate', 'completed', '质检: 9.2分 | 术语9.5·语义9.0·代码10.0·流畅9.0·风格8.5'],
  [400, 'restore', 'in_progress', '正在还原不可译区域…'],
  [700, 'restore', 'completed', '还原5处占位符'],
  [300, 'align', 'in_progress', '正在句级对齐…'],
  [600, 'align', 'completed', '对齐28个句对'],
  [400, 'learn', 'in_progress', '正在更新知识库…'],
  [800, 'learn', 'completed', '新增术语+3 · TM+12'],
  [300, 'export', 'in_progress', '正在准备导出…'],
  [500, 'export', 'completed', '翻译完成，耗时 17秒'],
];

function createInitialSteps(): Record<StepKey, StepState> {
  const steps = {} as Record<StepKey, StepState>;
  for (const key of STEP_ORDER) {
    steps[key] = 'pending';
  }
  return steps;
}

// ── Hook ──

export function useMockTranslate(): UseTranslateSSEReturn {
  const [steps, setSteps] = useState<Record<StepKey, StepState>>(createInitialSteps);
  const [currentStep, setCurrentStep] = useState<StepKey | null>(null);
  const [currentMessage, setCurrentMessage] = useState('');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('idle');

  const [strategy, setStrategy] = useState<StrategyBook | null>(null);
  const [termsSummary, setTermsSummary] = useState<UseTranslateSSEReturn['termsSummary']>(null);
  const [pendingTerms, setPendingTerms] = useState<TermEntry[]>([]);
  const [draftChunks, setDraftChunks] = useState<Array<{ chunk_id: string; text_chunk: string }>>([]);
  const [qaResult, setQaResult] = useState<QAResult | null>(null);
  const [finalText, setFinalText] = useState('');
  const [evolution, setEvolution] = useState<EvolutionReport | null>(null);
  const [exportFormats, setExportFormats] = useState<ExportFormat[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [realSessionId, setRealSessionId] = useState<string | null>(null);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutIds = useRef<ReturnType<typeof setTimeout>[]>([]);

  // ── 清理 ──

  const clearAllTimeouts = useCallback(() => {
    timeoutIds.current.forEach(clearTimeout);
    timeoutIds.current = [];
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // ── 启动 ──

  const start = useCallback(
    (_fileId: string, _userId: string = 'demo_user') => {
      // 重置所有状态
      clearAllTimeouts();
      setSteps(createInitialSteps());
      setCurrentStep(null);
      setCurrentMessage('');
      setElapsedSeconds(0);
      setConnectionStatus('connecting');
      setStrategy(null);
      setTermsSummary(null);
      setPendingTerms([]);
      setDraftChunks([]);
      setQaResult(null);
      setFinalText('');
      setEvolution(null);
      setExportFormats([]);
      setError(null);
      setRealSessionId(null);

      // 模拟连接建立
      const connectId = setTimeout(() => {
        setConnectionStatus('connected');
      }, 300);
      timeoutIds.current.push(connectId);

      // 启动计时
      timerRef.current = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);

      // 按序列逐步触发事件
      let cumulativeDelay = 500;
      for (const [delay, step, state, message] of MOCK_PROGRESS) {
        cumulativeDelay += delay;
        const id = setTimeout(() => {
          setSteps((prev) => ({ ...prev, [step]: state }));
          setCurrentStep(step);
          setCurrentMessage(message);

          // 当特定步骤完成时触发额外事件
          if (step === 'pre_translate' && state === 'completed') {
            setStrategy(MOCK_STRATEGY);
            setTermsSummary({
              total_terms: 8,
              rag_hit: 5,
              web_search: 0,
              pending: 0,
            });
            setPendingTerms(MOCK_TERMS.filter((t) => t.confidence === 'medium'));
          }
          if (step === 'translate' && state === 'in_progress') {
            setDraftChunks([
              { chunk_id: 'chunk_1', text_chunk: '# Kubernetes Pod 概述\n\n## 什么是 Pod\n\nPod 是 Kubernetes 中最小的...' },
            ]);
          }
          if (step === 'post_translate' && state === 'completed') {
            setQaResult(MOCK_QA);
            setFinalText(MOCK_FINAL);
          }
          if (step === 'learn' && state === 'completed') {
            setEvolution(MOCK_EVOLUTION);
          }
          if (step === 'export' && state === 'completed') {
            setExportFormats(['docx', 'html', 'bilingual']);
            setRealSessionId('mock_session_001');
            setConnectionStatus('disconnected');
            setElapsedSeconds(17); // 用模拟值替换计时器
            if (timerRef.current) {
              clearInterval(timerRef.current);
              timerRef.current = null;
            }
          }
        }, cumulativeDelay);
        timeoutIds.current.push(id);
      }
    },
    [clearAllTimeouts]
  );

  // ── 中止 ──

  const abort = useCallback(() => {
    clearAllTimeouts();
    setConnectionStatus('disconnected');
  }, [clearAllTimeouts]);

  // ── 清除待确认术语（确认提交后调用）──
  const clearPendingTerms = useCallback(() => {
    setPendingTerms([]);
  }, []);

  return {
    steps,
    currentStep,
    currentMessage,
    elapsedSeconds,
    connectionStatus,
    strategy,
    termsSummary,
    pendingTerms,
    draftChunks,
    qaResult,
    finalText,
    evolution,
    exportFormats,
    error,
    realSessionId,
    start,
    abort,
    clearPendingTerms,
  };
}
