/**
 * useTranslateSSE Hook
 * =====================
 * 通过 Fetch ReadableStream 消费 /api/translate 的 SSE 事件流。
 * 管理翻译全流程状态。
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

// ── 返回类型 ──

export type ConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'disconnected'
  | 'error';

export interface UseTranslateSSEReturn {
  /** 10个步骤的状态 */
  steps: Record<StepKey, StepState>;
  /** 当前正在执行的步骤 */
  currentStep: StepKey | null;
  /** 最新的进度消息 */
  currentMessage: string;
  /** 耗时（秒） */
  elapsedSeconds: number;
  /** SSE 连接状态 */
  connectionStatus: ConnectionStatus;

  /** 译前：策略书 */
  strategy: StrategyBook | null;
  /** 译前：术语摘要 */
  termsSummary: {
    total_terms: number;
    rag_hit: number;
    web_search: number;
    pending: number;
  } | null;
  /** 待确认术语 */
  pendingTerms: TermEntry[];
  /** 初译稿 chunk 列表 */
  draftChunks: Array<{ chunk_id: string; text_chunk: string }>;
  /** 质检报告 */
  qaResult: QAResult | null;
  /** 终稿 */
  finalText: string;
  /** 进化报告 */
  evolution: EvolutionReport | null;
  /** 可导出格式 */
  exportFormats: ExportFormat[];
  /** 错误信息 */
  error: string | null;
  /** 后端返回的真实 session_id */
  realSessionId: string | null;

  /** 启动 SSE 连接 */
  start: (fileId: string, userId?: string) => void;
  /** 中止翻译 */
  abort: () => void;
  /** 清除待确认术语（用户已提交确认后调用） */
  clearPendingTerms: () => void;
}

// ── 初始步骤状态 ──

function createInitialSteps(): Record<StepKey, StepState> {
  const steps = {} as Record<StepKey, StepState>;
  for (const key of STEP_ORDER) {
    steps[key] = 'pending';
  }
  return steps;
}

/** API 基础地址 */
function getBaseUrl(): string {
  return (
    (import.meta as Record<string, unknown>).env?.VITE_API_BASE_URL as string ||
    'http://localhost:8000'
  );
}

// ── Hook ──

export function useTranslateSSE(): UseTranslateSSEReturn {
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

  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── 计时器 ──

  const startTimer = useCallback(() => {
    stopTimer();
    setElapsedSeconds(0);
    timerRef.current = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // ── 步骤更新 ──

  const updateStep = useCallback(
    (step: StepKey, state: StepState, message: string) => {
      setSteps((prev) => ({ ...prev, [step]: state }));
      setCurrentStep(step);
      setCurrentMessage(message);
    },
    []
  );

  // ── 事件处理 ──

  const processEvent = useCallback(
    (eventType: string, data: Record<string, unknown>) => {
      switch (eventType) {
        case 'progress': {
          updateStep(
            data.step as StepKey,
            data.state as StepState,
            data.message as string
          );
          break;
        }
        case 'strategy': {
          setStrategy(data as unknown as StrategyBook);
          break;
        }
        case 'terms': {
          setTermsSummary({
            total_terms: data.total_terms as number,
            rag_hit: data.rag_hit as number,
            web_search: data.web_search as number,
            pending: data.pending as number,
          });
          break;
        }
        case 'terms_pending': {
          // 术语确认断点：填充待确认术语详情 + 记录真实 session_id
          setPendingTerms(data.pending_terms as TermEntry[]);
          if (data.session_id) {
            setRealSessionId(data.session_id as string);
          }
          break;
        }
        case 'draft': {
          setDraftChunks((prev) => [
            ...prev,
            {
              chunk_id: data.chunk_id as string,
              text_chunk: data.text_chunk as string,
            },
          ]);
          break;
        }
        case 'qa': {
          setQaResult(data as unknown as QAResult);
          break;
        }
        case 'final': {
          setFinalText(data.final_text as string);
          setRealSessionId(data.session_id as string);
          break;
        }
        case 'evolution': {
          setEvolution(data as unknown as EvolutionReport);
          break;
        }
        case 'error': {
          setError(data.message as string);
          setConnectionStatus('error');
          stopTimer();
          break;
        }
        case 'done': {
          setExportFormats(data.export_formats as ExportFormat[]);
          setElapsedSeconds(data.elapsed_seconds as number);
          setConnectionStatus('disconnected');
          stopTimer();
          break;
        }
      }
    },
    [updateStep, stopTimer]
  );

  // ── 启动 SSE ──

  const start = useCallback(
    (fileId: string, userId: string = 'demo_user') => {
      // 重置所有状态
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

      // 中止之前的连接
      abortRef.current?.abort();
      // 记录本次请求的 controller，用于后续判断「过期请求」：
      // StrictMode 双执行 effect 时，第一次 start 会被 cleanup 的 abort()
      // 取消，其 catch 在微任务中异步执行——若此时已由第二次 start 创建了
      // 新连接，旧 catch 必须忽略（否则会停掉新连接的计时器、覆盖连接状态）。
      const controller = new AbortController();
      abortRef.current = controller;

      // 启动计时
      startTimer();

      const formData = new FormData();
      formData.append('file_id', fileId);
      formData.append('user_id', userId);

      fetch(`${getBaseUrl()}/api/translate`, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      })
        .then(async (response) => {
          // 已被更新的 start 取代 → 忽略过期响应
          if (abortRef.current !== controller) return;

          if (!response.ok) {
            throw new Error(`翻译请求失败: HTTP ${response.status}`);
          }

          setConnectionStatus('connected');

          const reader = response.body?.getReader();
          if (!reader) {
            throw new Error('浏览器不支持 ReadableStream');
          }

          const decoder = new TextDecoder();
          let buffer = '';
          let currentEventType = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // 已被更新的 start 取代 → 停止读取过期流
            if (abortRef.current !== controller) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            // 保留最后一个不完整的行
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('event: ')) {
                currentEventType = line.slice(7).trim();
              } else if (line.startsWith('data: ')) {
                try {
                  const data = JSON.parse(line.slice(6));
                  processEvent(currentEventType, data);
                } catch {
                  // 跳过解析失败的行
                  console.warn('SSE 解析失败:', line);
                }
              }
              // 空行表示事件边界 — 重置
              if (line === '') {
                currentEventType = '';
              }
            }
          }
        })
        .catch((err) => {
          // 过期请求（被更新的 start 取代）：忽略，不干扰新连接
          if (abortRef.current !== controller) return;

          if (err.name === 'AbortError') {
            setConnectionStatus('disconnected');
          } else {
            setError(err.message);
            setConnectionStatus('error');
          }
          stopTimer();
        });
    },
    [startTimer, stopTimer, processEvent]
  );

  // ── 中止 ──

  const abort = useCallback(() => {
    abortRef.current?.abort();
    stopTimer();
    setConnectionStatus('disconnected');
  }, [stopTimer]);

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
