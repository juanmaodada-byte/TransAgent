/**
 * useMockTranslate Hook
 * =====================
 * 用 setTimeout 模拟 SSE 事件序列，支持前端独立开发和演示。
 * 接口与 useTranslateSSE 一致，在 TranslatePage 中通过
 * 环境变量 VITE_USE_MOCK=true 切换。
 *
 * Mock 特性：
 *  - 术语确认「暂停」：到达术语确认断点后停止后续步骤，等待 resume() 继续
 *  - 终稿/初译稿「回显用户输入」（真实翻译请切 VITE_USE_MOCK=false）
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
import type { ConnectionStatus, UseTranslateSSEReturn, UseTranslateSSEOptions } from './useTranslateSSE';

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

/** 无输入文本时的兜底终稿（有输入时回显输入） */
const MOCK_DEFAULT_FINAL = `# Mock 演示文档

> 📌 当前为 Mock 演示模式：未提供输入文本，展示默认示例。
> 粘贴你的原文后，Mock 会回显输入内容；真实翻译请切换 VITE_USE_MOCK=false。`;

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

/** 术语确认断点之前的步骤（自动执行） */
const MOCK_PROGRESS_PRE: MockStep[] = [
  [500, 'input_detect', 'in_progress', '正在检测文件格式…'],
  [800, 'input_detect', 'completed', '格式检测完成: Markdown'],
  [400, 'input_convert', 'in_progress', '正在解析文档结构…'],
  [1000, 'input_convert', 'completed', '预处理完成: 3200 tokens | 3 chunks | 占位符 5处'],
  [600, 'pre_translate', 'in_progress', '译前Sub-Agent工作中（策略+术语）…'],
  [1500, 'pre_translate', 'completed', 'ICT子领域: Kubernetes/云原生 | 术语: 8个'],
];

/** 术语确认断点之后的步骤（resume() 后继续） */
const MOCK_PROGRESS_POST: MockStep[] = [
  [500, 'terminology_confirm', 'completed', '术语已确认，继续翻译'],
  [800, 'translate', 'in_progress', '译中Sub-Agent工作中（串行·3 chunk）…'],
  [2500, 'translate', 'completed', '初译完成 | 一致性: 预检通过'],
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

export function useMockTranslate(options?: UseTranslateSSEOptions): UseTranslateSSEReturn & {
  resume: () => void;
} {
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
  const sourceTextRef = useRef<string | undefined>(undefined);
  const resumedRef = useRef(false);

  // onEvent 用 ref 保持稳定
  const onEventRef = useRef(options?.onEvent);
  onEventRef.current = options?.onEvent;

  // ── 清理 ──

  const clearAllTimeouts = useCallback(() => {
    timeoutIds.current.forEach(clearTimeout);
    timeoutIds.current = [];
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // ── 调度工具 ──

  const scheduleSteps = useCallback(
    (
      seq: MockStep[],
      baseDelay: number,
      onStep: (step: StepKey, state: StepState, message: string, index: number) => void
    ) => {
      let cumulative = baseDelay;
      seq.forEach(([delay, step, state, message], index) => {
        cumulative += delay;
        const id = setTimeout(() => onStep(step, state, message, index), cumulative);
        timeoutIds.current.push(id);
      });
      return cumulative;
    },
    []
  );

  /** 执行单个步骤 + 触发对应事件 */
  const runStep = useCallback(
    (step: StepKey, state: StepState, message: string, index: number) => {
      setSteps((prev) => ({ ...prev, [step]: state }));
      setCurrentStep(step);
      setCurrentMessage(message);
      onEventRef.current?.({ type: 'progress', step, state, message });

      const sourceText = sourceTextRef.current;
      const finalContent = sourceText?.trim()
        ? `> 📌 Mock 演示模式：以下为输入原文回显（真实翻译请切换 VITE_USE_MOCK=false）\n\n${sourceText}`
        : MOCK_DEFAULT_FINAL;

      if (step === 'pre_translate' && state === 'completed') {
        setStrategy(MOCK_STRATEGY);
        setTermsSummary({ total_terms: 8, rag_hit: 5, web_search: 0, pending: 0 });
        onEventRef.current?.({ type: 'strategy', ...MOCK_STRATEGY });
        onEventRef.current?.({
          type: 'terms', total_terms: 8, rag_hit: 5, web_search: 0, pending: 0,
        });
        const pending = MOCK_TERMS.filter((t) => t.confidence === 'medium');
        setPendingTerms(pending);
        onEventRef.current?.({
          type: 'terms_pending',
          session_id: 'mock_session_001',
          pending_terms: pending,
        });
      }
      if (step === 'translate' && state === 'in_progress') {
        const chunk = {
          chunk_id: 'chunk_1',
          text_chunk: sourceText?.trim() ? sourceText : MOCK_DEFAULT_FINAL,
        };
        setDraftChunks([chunk]);
        onEventRef.current?.({ type: 'draft', chunk_id: chunk.chunk_id, text_chunk: chunk.text_chunk });
      }
      if (step === 'post_translate' && state === 'completed') {
        setQaResult(MOCK_QA);
        setFinalText(finalContent);
        onEventRef.current?.({ type: 'qa', ...MOCK_QA });
        onEventRef.current?.({
          type: 'final', final_text: finalContent, session_id: 'mock_session_001',
        });
      }
      if (step === 'learn' && state === 'completed') {
        setEvolution(MOCK_EVOLUTION);
        onEventRef.current?.({ type: 'evolution', ...MOCK_EVOLUTION });
      }
      if (step === 'export' && state === 'completed') {
        setExportFormats(['md', 'docx', 'html', 'bilingual']);
        setRealSessionId('mock_session_001');
        setConnectionStatus('disconnected');
        setElapsedSeconds(17);
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
        onEventRef.current?.({
          type: 'done',
          session_id: 'mock_session_001',
          elapsed_seconds: 17,
          export_formats: ['md', 'docx', 'html', 'bilingual'],
        });
      }
    },
    []
  );

  // ── 启动 ──

  const start = useCallback(
    (_fileId: string, _userId: string = 'demo_user', sourceText?: string) => {
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
      sourceTextRef.current = sourceText;
      resumedRef.current = false;

      // 模拟连接建立
      const connectId = setTimeout(() => setConnectionStatus('connected'), 300);
      timeoutIds.current.push(connectId);

      // 启动计时
      timerRef.current = setInterval(() => setElapsedSeconds((prev) => prev + 1), 1000);

      // 调度「确认断点之前」的步骤；到 terms_pending 后暂停等待 resume()
      scheduleSteps(MOCK_PROGRESS_PRE, 500, runStep);
    },
    [clearAllTimeouts, scheduleSteps, runStep]
  );

  /** 术语确认后继续（Mock 的「确认断点」恢复） */
  const resume = useCallback(() => {
    if (resumedRef.current) return;
    resumedRef.current = true;
    scheduleSteps(MOCK_PROGRESS_POST, 0, runStep);
  }, [scheduleSteps, runStep]);

  // ── 中止 ──

  const abort = useCallback(() => {
    clearAllTimeouts();
    setConnectionStatus('disconnected');
  }, [clearAllTimeouts]);

  // ── 清除待确认术语 ──

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
    resume,
  };
}
