/**
 * useProjectRunner Hook
 * =====================
 * 翻译运行器：把 SSE 事件流转换为「对话消息 + 项目文件」写入当前项目。
 *
 * 事件映射：
 *   progress      → 原地更新运行中的进度消息（累积 steps 快照）
 *   strategy      → 追加策略消息 + 写 strategy 报告文件
 *   terms_pending → 追加确认消息（TermConfirmCard）+ record 置 waiting
 *   draft         → 累积初译稿消息 + upsert draft 文件
 *   qa            → 追加质检消息 + 写 qa 报告文件
 *   final         → 追加终稿消息 + upsert final 文件
 *   evolution     → 追加进化消息 + 写 evolution 报告文件
 *   done          → record 完成 + 写 terms 知识文件 + 补充 exportFormats
 *   error         → 追加错误消息 + record 置 error
 */

import { useRef, useCallback, useEffect } from 'react';
import { useTranslateSSE, type UseTranslateSSEReturn } from './useTranslateSSE';
import { useMockTranslate } from './useMockTranslate';
import { useProjectsContext } from '../context/ProjectsContext';
import { isMockMode, mockConfirmTerms } from '../api/mock';
import { confirmTerms, confirmDraft as confirmDraftApi } from '../api/client';
import { genId } from './useProjects';
import { STEP_ORDER } from '../types';
import type {
  ProgressMessageData,
  TranslationRecord,
} from '../types/project';
import type {
  StepKey,
  StepState,
  TermEntry,
  StrategyBook,
  QAResult,
  EvolutionReport,
  ExportFormat,
} from '../types';

export interface StartInput {
  kind: 'file' | 'paste';
  title: string;
  fileId: string;
  /** 源文预览（source 文件内容） */
  sourcePreview?: string | null;
}

/** D9：终稿确认后可用的导出格式（done 事件缺失时兜底用） */
const DEFAULT_EXPORT_FORMATS: ExportFormat[] = ['md', 'docx', 'html', 'bilingual'];

export interface ProjectRunnerApi {
  startTranslation: (input: StartInput) => void;
  confirmPendingTerms: (msgId: string, confirmed: TermEntry[]) => Promise<void>;
  skipPendingTerms: (msgId: string) => void;
  /** D8.1 MVP：确认译中初译（中英对照），唤醒翻译继续译后 */
  confirmDraft: (msgId: string) => Promise<void>;
  /** 确认终稿：解锁导出 + 应用知识沉淀 + 任务标完成 */
  confirmFinal: (msgId: string) => void;
  abort: () => void;
  activeRecordId: string | null;
  /** 是否运行中（供输入区禁用） */
  busy: boolean;
}

function createEmptySteps(): Record<StepKey, StepState> {
  const steps = {} as Record<StepKey, StepState>;
  for (const key of STEP_ORDER) steps[key] = 'pending';
  return steps;
}

export function useProjectRunner(): ProjectRunnerApi {
  const { actions, activeProjectId, activeProject } = useProjectsContext();
  const isMock = isMockMode();

  // ── 运行期状态（ref，避免闭包过期） ──
  const progressMsgIdRef = useRef<string | null>(null);
  const stepsRef = useRef<Record<StepKey, StepState>>(createEmptySteps());
  const currentStepRef = useRef<StepKey | null>(null);
  const startedAtRef = useRef(0);
  const draftMsgIdRef = useRef<string | null>(null);
  const draftChunksRef = useRef<Array<{ chunk_id: string; text_chunk: string }>>([]);
  const finalMsgIdRef = useRef<string | null>(null);
  const finalTextRef = useRef('');
  const sessionIdRef = useRef<string | null>(null);
  const confirmedTermsRef = useRef<TermEntry[]>([]);
  const activeRecordIdRef = useRef<string | null>(null);
  /** 运行心跳：整个翻译期间每秒刷新进度耗时（LLM 调用中事件稀疏，否则耗时停滞） */
  const runTickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** 最新进度消息文本（progress 事件时更新） */
  const currentMsgRef = useRef('🚀 正在准备翻译…');
  /** 是否处于等待术语确认（心跳显示等待提示） */
  const waitingConfirmRef = useRef(false);
  /** 终稿确认暂存：done/evolution 在用户确认前不落地 */
  const pendingExportFormatsRef = useRef<ExportFormat[]>([]);
  const pendingEvolutionRef = useRef<EvolutionReport | null>(null);
  const pendingDoneRef = useRef<{ elapsed: number } | null>(null);
  /** 三栏句对齐（终稿确认时保留在 final 消息 data） */
  const alignedRowsRef = useRef<Array<{
    source_seg: string;
    draft_seg: string;
    final_seg: string;
  }>>([]);

  // ── 事件处理器（每次渲染更新，稳定包装转发） ──
  const handlerRef = useRef<(event: { type: string } & Record<string, unknown>) => void>(() => {});
  handlerRef.current = (event) => {
    const { type, ...data } = event;
    const recordId = activeRecordIdRef.current;
    const now = Date.now();

    switch (type) {
      case 'progress': {
        if (!progressMsgIdRef.current) break;
        const step = data.step as StepKey;
        const state = data.state as StepState;
        // 累积 steps 快照
        if (step) stepsRef.current[step] = state;
        currentStepRef.current = step ?? currentStepRef.current;
        currentMsgRef.current = (data.message as string) ?? currentMsgRef.current;
        const elapsed = Math.floor((now - startedAtRef.current) / 1000);
        actions.updateMessage(progressMsgIdRef.current, {
          data: {
            steps: { ...stepsRef.current },
            currentStep: currentStepRef.current,
            currentMessage: currentMsgRef.current,
            elapsedSeconds: elapsed,
            connectionStatus: 'connected',
            sessionId: sessionIdRef.current,
          } as ProgressMessageData,
        } as never);
        break;
      }

      case 'strategy': {
        actions.appendMessage({
          id: genId('msg_'),
          role: 'assistant',
          type: 'strategy',
          recordId,
          createdAt: now,
          data: data as unknown as StrategyBook,
        });
        actions.addFile({
          id: genId('file_'),
          kind: 'strategy',
          category: 'reports',
          name: '翻译策略书.json',
          content: JSON.stringify(data, null, 2),
          format: 'json',
          sizeKb: Math.round(JSON.stringify(data).length / 1024),
          createdAt: now,
          recordId,
        });
        break;
      }

      case 'terms_pending': {
        const pendingTerms = (data.pending_terms as TermEntry[]) ?? [];
        const msgId = genId('msg_');
        sessionIdRef.current = (data.session_id as string) ?? sessionIdRef.current;
        waitingConfirmRef.current = true;
        actions.appendMessage({
          id: msgId,
          role: 'assistant',
          type: 'terms_pending',
          recordId,
          createdAt: now,
          status: 'waiting',
          data: {
            terms: pendingTerms,
            sessionId: sessionIdRef.current,
            resolved: null,
          },
        });
        if (recordId) actions.setRecordStatus(recordId, 'waiting');
        break;
      }

      case 'draft_pending': {
        // D8.1 MVP：译中完成 → 中英对照确认断点（DraftConfirmCard）
        const rows = (data.rows as Array<{ source_seg: string; target_seg: string; chunk_id?: string }>) ?? [];
        const msgId = genId('msg_');
        sessionIdRef.current = (data.session_id as string) ?? sessionIdRef.current;
        waitingConfirmRef.current = true;
        actions.appendMessage({
          id: msgId,
          role: 'assistant',
          type: 'draft_pending',
          recordId,
          createdAt: now,
          status: 'waiting',
          data: {
            rows,
            sessionId: sessionIdRef.current,
            resolved: null,
          },
        });
        if (recordId) actions.setRecordStatus(recordId, 'waiting');
        break;
      }

      case 'draft': {
        draftChunksRef.current = [
          ...draftChunksRef.current,
          { chunk_id: data.chunk_id as string, text_chunk: data.text_chunk as string },
        ];
        const full = draftChunksRef.current.map((c) => c.text_chunk).join('\n\n');
        if (!draftMsgIdRef.current) {
          const msgId = genId('msg_');
          draftMsgIdRef.current = msgId;
          actions.appendMessage({
            id: msgId,
            role: 'assistant',
            type: 'draft',
            recordId,
            createdAt: now,
            data: { chunks: draftChunksRef.current, fullText: full },
          });
        } else {
          actions.updateMessage(draftMsgIdRef.current, {
            data: { chunks: draftChunksRef.current, fullText: full },
          } as never);
        }
        actions.upsertFile({
          id: `draft-${recordId ?? 'x'}`,
          kind: 'draft',
          category: 'outputs',
          name: '初译稿.md',
          content: full,
          format: 'md',
          sizeKb: Math.round(full.length / 1024),
          createdAt: now,
          recordId,
        });
        break;
      }

      case 'qa': {
        const qa = data as unknown as QAResult;
        actions.appendMessage({
          id: genId('msg_'),
          role: 'assistant',
          type: 'qa',
          recordId,
          createdAt: now,
          data: qa,
        });
        actions.addFile({
          id: genId('file_'),
          kind: 'qa',
          category: 'reports',
          name: '质检报告.json',
          content: JSON.stringify(qa, null, 2),
          format: 'json',
          sizeKb: Math.round(JSON.stringify(qa).length / 1024),
          createdAt: now,
          recordId,
        });
        break;
      }

      case 'final': {
        const finalText = data.final_text as string;
        const draftText = data.draft_text as string | undefined;
        if (draftText?.trim() && !draftMsgIdRef.current) {
          const draftMsgId = genId('msg_');
          draftMsgIdRef.current = draftMsgId;
          draftChunksRef.current = [{ chunk_id: 'draft', text_chunk: draftText }];
          actions.appendMessage({
            id: draftMsgId,
            role: 'assistant',
            type: 'draft',
            recordId,
            createdAt: now,
            data: { chunks: draftChunksRef.current, fullText: draftText },
          });
          actions.upsertFile({
            id: `draft-${recordId ?? 'x'}`,
            kind: 'draft',
            category: 'outputs',
            name: '初译稿.md',
            content: draftText,
            format: 'md',
            sizeKb: Math.round(draftText.length / 1024),
            createdAt: now,
            recordId,
          });
        }
        finalTextRef.current = finalText;
        sessionIdRef.current = (data.session_id as string) ?? sessionIdRef.current;
        // 三栏句对齐（源|初译|终译）
        const alignedRows = (data.aligned_rows as Array<{
          source_seg: string;
          draft_seg: string;
          final_seg: string;
        }>) ?? [];
        alignedRowsRef.current = alignedRows;
        const msgId = genId('msg_');
        finalMsgIdRef.current = msgId;
        actions.appendMessage({
          id: msgId,
          role: 'assistant',
          type: 'final',
          recordId,
          createdAt: now,
          data: {
            finalText,
            sessionId: sessionIdRef.current,
            exportFormats: [],
            alignedRows,
          },
        });
        actions.upsertFile({
          id: `final-${recordId ?? 'x'}`,
          kind: 'final',
          category: 'outputs',
          name: '翻译终稿.md',
          content: finalText,
          format: 'md',
          sizeKb: Math.round(finalText.length / 1024),
          createdAt: now,
          recordId,
          sessionId: sessionIdRef.current,
          exportFormats: [],
        });
        break;
      }

      case 'evolution': {
        // 终稿确认前暂存进化报告（确认后才展示为「已沉淀」）
        pendingEvolutionRef.current = data as unknown as EvolutionReport;
        break;
      }

      case 'done': {
        // 终稿确认前暂存 done 信息：不标完成、不解锁导出（等待 confirmFinal）
        pendingExportFormatsRef.current = (data.export_formats as ExportFormat[]) ?? [];
        pendingDoneRef.current = { elapsed: data.elapsed_seconds as number };
        // record 保持 waiting（终稿待确认）
        if (recordId) actions.setRecordStatus(recordId, 'waiting');
        break;
      }

      case 'error': {
        const msg = (data.message as string) ?? '翻译失败';
        const degraded = data.code === 'degraded';  // D9.1：降级非致命，继续展示结果
        actions.appendMessage({
          id: genId('msg_'),
          role: 'assistant',
          type: degraded ? 'degraded' : 'error',
          recordId,
          createdAt: now,
          data: { message: msg },
        });
        if (degraded) break;  // 不标错误、不停心跳——后面还有 draft/final 事件
        if (recordId) actions.setRecordStatus(recordId, 'error');
        if (progressMsgIdRef.current) {
          actions.updateMessage(progressMsgIdRef.current, { status: 'error' } as never);
        }
        if (runTickRef.current) {
          clearInterval(runTickRef.current);
          runTickRef.current = null;
        }
        break;
      }
    }
  };

  const stableOnEvent = useCallback(
    (event: { type: string } & Record<string, unknown>) => {
      handlerRef.current(event);
    },
    []
  );

  // SSE hook（onEvent 稳定包装 → handlerRef 最新闭包）
  const realSSE = useTranslateSSE({ onEvent: stableOnEvent });
  const mockSSE = useMockTranslate({ onEvent: stableOnEvent });
  const sse: UseTranslateSSEReturn = isMock ? mockSSE : realSSE;

  // ── API ──

  const startTranslation = useCallback(
    (input: StartInput) => {
      // 清理上一次运行状态
      if (runTickRef.current) {
        clearInterval(runTickRef.current);
        runTickRef.current = null;
      }
      progressMsgIdRef.current = null;
      draftMsgIdRef.current = null;
      draftChunksRef.current = [];
      finalMsgIdRef.current = null;
      confirmedTermsRef.current = [];
      sessionIdRef.current = null;
      stepsRef.current = createEmptySteps();
      currentStepRef.current = null;
      startedAtRef.current = Date.now();
      activeRecordIdRef.current = input.fileId;

      const now = Date.now();

      // 翻译记录
      const record: TranslationRecord = {
        id: input.fileId,
        title: input.title,
        inputKind: input.kind,
        fileId: input.fileId,
        realSessionId: null,
        status: 'running',
        createdAt: now,
      };
      actions.addRecord(record);

      // 用户消息
      actions.appendMessage({
        id: genId('msg_'),
        role: 'user',
        recordId: input.fileId,
        createdAt: now,
        data: { kind: input.kind, title: input.title, fileId: input.fileId },
      });

      // 源文文件
      actions.addFile({
        id: `source-${input.fileId}`,
        kind: 'source',
        category: 'outputs',
        name: input.title,
        content: input.sourcePreview ?? '（源文预览由后端提供）',
        format: 'text',
        createdAt: now,
        recordId: input.fileId,
      });

      // 运行中进度消息（后续原地更新）
      const progressMsgId = genId('msg_');
      progressMsgIdRef.current = progressMsgId;
      actions.appendMessage({
        id: progressMsgId,
        role: 'assistant',
        type: 'progress',
        recordId: input.fileId,
        createdAt: now,
        status: 'running',
        data: {
          steps: { ...stepsRef.current },
          currentStep: null,
          currentMessage: '🚀 正在准备翻译…',
          elapsedSeconds: 0,
          connectionStatus: 'connecting',
          sessionId: input.fileId,
        },
      });

      // 启动 SSE（Mock 模式传 sourceText 用于回显）
      (sse.start as (a: string, b?: string, c?: string) => void)(
        input.fileId,
        'demo_user',
        input.sourcePreview ?? undefined
      );

      // 运行心跳：整个翻译期间每秒刷新耗时（LLM 调用中事件稀疏，否则耗时停滞）
      if (runTickRef.current) clearInterval(runTickRef.current);
      runTickRef.current = setInterval(() => {
        if (!progressMsgIdRef.current) return;
        const elapsed = Math.floor((Date.now() - startedAtRef.current) / 1000);
        actions.updateMessage(progressMsgIdRef.current, {
          data: {
            steps: { ...stepsRef.current },
            currentStep: currentStepRef.current,
            currentMessage: waitingConfirmRef.current
              ? '⏸️ 等待术语确认…'
              : currentMsgRef.current,
            elapsedSeconds: elapsed,
            connectionStatus: 'connected',
            sessionId: sessionIdRef.current,
          } as ProgressMessageData,
        } as never);
      }, 1000);
    },
    [actions, sse]
  );

  const confirmPendingTerms = useCallback(
    async (msgId: string, confirmed: TermEntry[]) => {
      confirmedTermsRef.current = [...confirmedTermsRef.current, ...confirmed];
      const sessionId = sessionIdRef.current;
      try {
        if (isMock) {
          await mockConfirmTerms(sessionId ?? 'mock', confirmed);
        } else if (sessionId) {
          await confirmTerms(sessionId, confirmed);
        }
      } catch (err) {
        console.error('术语确认提交失败:', err);
      }
      actions.updateMessage(msgId, {
        status: 'done',
        data: {
          terms: confirmed,
          sessionId,
          resolved: 'confirmed',
        },
      } as never);
      sse.clearPendingTerms();
      waitingConfirmRef.current = false;
      // Mock 模式：恢复「确认断点」之后的步骤
      if (isMock) {
        (sse as unknown as { resume?: () => void }).resume?.();
      }
      if (activeRecordIdRef.current) {
        actions.setRecordStatus(activeRecordIdRef.current, 'running');
      }
    },
    [actions, isMock, sse]
  );

  const skipPendingTerms = useCallback(
    (msgId: string) => {
      actions.updateMessage(msgId, {
        status: 'done',
        data: {
          terms: [],
          sessionId: sessionIdRef.current,
          resolved: 'skipped',
        },
      } as never);
      sse.clearPendingTerms();
      waitingConfirmRef.current = false;
      if (activeRecordIdRef.current) {
        actions.setRecordStatus(activeRecordIdRef.current, 'running');
      }
    },
    [actions, sse]
  );

  /** D8.1 MVP：确认译中初译（中英对照）→ 唤醒翻译继续译后 */
  const confirmDraft = useCallback(
    async (msgId: string) => {
      const sessionId = sessionIdRef.current;
      // 保留中英对照行（消息是可靠数据源）
      const existingMsg = activeProject?.messages.find((m) => m.id === msgId);
      const rows =
        existingMsg && existingMsg.role === 'assistant' && existingMsg.type === 'draft_pending'
          ? existingMsg.data.rows
          : [];
      try {
        if (isMock) {
          (sse as unknown as { resume?: () => void }).resume?.();
        } else if (sessionId) {
          await confirmDraftApi(sessionId);
        }
      } catch (err) {
        console.error('初译确认提交失败:', err);
      }
      actions.updateMessage(msgId, {
        status: 'done',
        data: {
          rows,
          sessionId,
          resolved: 'confirmed',
        },
      } as never);
      waitingConfirmRef.current = false;
      if (activeRecordIdRef.current) {
        actions.setRecordStatus(activeRecordIdRef.current, 'running');
      }
    },
    [actions, activeProject?.messages, isMock, sse]
  );

  /** 确认终稿：解锁导出 + 应用知识沉淀 + 任务标完成 */
  const confirmFinal = useCallback(
    (msgId: string) => {
      // D9：done 事件缺失（如 LLM 空响应降级路径）时兜底提供导出格式，避免「无导出选项」
      const formats =
        pendingExportFormatsRef.current.length > 0
          ? pendingExportFormatsRef.current
          : DEFAULT_EXPORT_FORMATS;
      const sessionId = sessionIdRef.current;
      const recordId = activeRecordIdRef.current;
      const now = Date.now();

      // 保留三栏句对齐：优先取当前 final 消息里已有的（消息是可靠数据源），ref 兜底
      const existingMsg = activeProject?.messages.find((m) => m.id === msgId);
      const existingAligned =
        (existingMsg && existingMsg.role === 'assistant' && existingMsg.type === 'final'
          ? existingMsg.data.alignedRows
          : undefined) ?? alignedRowsRef.current;

      // 1. final 消息：标记已确认 + 补导出格式 + 保留句对齐
      actions.updateMessage(msgId, {
        data: {
          finalText: finalTextRef.current,
          sessionId,
          exportFormats: formats,
          confirmed: true,
          alignedRows: existingAligned,
        },
      } as never);

      // 2. final 文件：补导出格式
      if (recordId) {
        actions.upsertFile({
          id: `final-${recordId}`,
          kind: 'final',
          category: 'outputs',
          name: '翻译终稿.md',
          content: finalTextRef.current,
          format: 'md',
          sizeKb: Math.round(finalTextRef.current.length / 1024),
          createdAt: now,
          recordId,
          sessionId,
          exportFormats: formats,
        });
      }

      // 3. 展示暂存的进化报告（知识沉淀）
      if (pendingEvolutionRef.current) {
        actions.appendMessage({
          id: genId('msg_'),
          role: 'assistant',
          type: 'evolution',
          recordId,
          createdAt: now,
          data: pendingEvolutionRef.current,
        });
        actions.addFile({
          id: genId('file_'),
          kind: 'evolution',
          category: 'reports',
          name: '进化报告.json',
          content: JSON.stringify(pendingEvolutionRef.current, null, 2),
          format: 'json',
          sizeKb: Math.round(JSON.stringify(pendingEvolutionRef.current).length / 1024),
          createdAt: now,
          recordId,
        });
      }

      // 4. 写 terms 知识文件（已确认术语）
      if (confirmedTermsRef.current.length > 0) {
        const rows = confirmedTermsRef.current.map(
          (t) => `${t.term}\t${t.translation}\t${t.action}\t${t.source}`
        );
        actions.upsertFile({
          id: `terms-${recordId ?? 'x'}`,
          kind: 'terms',
          category: 'knowledge',
          name: '术语表.tsv',
          content: `term\ttranslation\taction\tsource\n${rows.join('\n')}`,
          format: 'text',
          sizeKb: Math.round(rows.join('\n').length / 1024),
          createdAt: now,
          recordId,
        });
      }

      // 5. record 标完成
      if (recordId) {
        actions.updateRecord(recordId, {
          status: 'done',
          realSessionId: sessionId,
          completedAt: now,
          elapsedSeconds: pendingDoneRef.current?.elapsed ?? undefined,
        });
      }

      // 6. 进度消息标 done + 停止心跳
      if (progressMsgIdRef.current) {
        actions.updateMessage(progressMsgIdRef.current, { status: 'done' } as never);
      }
      if (runTickRef.current) {
        clearInterval(runTickRef.current);
        runTickRef.current = null;
      }

      // 清理暂存
      pendingExportFormatsRef.current = [];
      pendingEvolutionRef.current = null;
      pendingDoneRef.current = null;
    },
    [actions, activeProject]
  );

  /** 用户点击"停止"：中止当前翻译，标记为已停止（而非失败） */
  const abort = useCallback(() => {
    sse.abort();
    // 停止运行心跳
    if (runTickRef.current) {
      clearInterval(runTickRef.current);
      runTickRef.current = null;
    }
    if (activeRecordIdRef.current) {
      actions.setRecordStatus(activeRecordIdRef.current, 'aborted');
    }
    if (progressMsgIdRef.current) {
      actions.updateMessage(progressMsgIdRef.current, {
        status: 'aborted',
        data: {
          steps: { ...stepsRef.current },
          currentStep: currentStepRef.current,
          currentMessage: '⛔ 翻译已停止',
          elapsedSeconds: Math.floor((Date.now() - startedAtRef.current) / 1000),
          connectionStatus: 'disconnected' as never,
          sessionId: sessionIdRef.current,
        },
      } as never);
    }
    // 清理本次运行的中间状态（startTranslation 也会重置，双保险）
    progressMsgIdRef.current = null;
    draftMsgIdRef.current = null;
    draftChunksRef.current = [];
    finalMsgIdRef.current = null;
    finalTextRef.current = '';
    sessionIdRef.current = null;
    confirmedTermsRef.current = [];
    waitingConfirmRef.current = false;
  }, [actions, sse]);

  // ── 项目切换：清理运行状态，避免串扰 ──
  // 切换项目时，中止当前翻译连接并重置 refs（当前项目数据保留在 localStorage）。
  const prevProjectIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevProjectIdRef.current;
    prevProjectIdRef.current = activeProjectId ?? null;
    if (prev === null || prev === activeProjectId) return;

    // 项目已切换：中止当前翻译
    if (activeRecordIdRef.current) {
      actions.setRecordStatus(activeRecordIdRef.current, 'error');
      if (progressMsgIdRef.current) {
        actions.updateMessage(progressMsgIdRef.current, {
          status: 'error',
          data: {
            steps: { ...stepsRef.current },
            currentStep: currentStepRef.current,
            currentMessage: '⛔ 翻译已中断（切换了项目）',
            elapsedSeconds: Math.floor((Date.now() - startedAtRef.current) / 1000),
            connectionStatus: 'disconnected' as never,
            sessionId: sessionIdRef.current,
          },
        } as never);
      }
    }
    sse.abort();
    // 重置运行状态
    if (runTickRef.current) {
      clearInterval(runTickRef.current);
      runTickRef.current = null;
    }
    progressMsgIdRef.current = null;
    draftMsgIdRef.current = null;
    draftChunksRef.current = [];
    finalMsgIdRef.current = null;
    finalTextRef.current = '';
    sessionIdRef.current = null;
    confirmedTermsRef.current = [];
    activeRecordIdRef.current = null;
    pendingExportFormatsRef.current = [];
    pendingEvolutionRef.current = null;
    pendingDoneRef.current = null;
    alignedRowsRef.current = [];
    waitingConfirmRef.current = false;
    stepsRef.current = createEmptySteps();
    currentStepRef.current = null;
  }, [activeProjectId, actions, sse]);

  // ── busy 从当前项目的翻译记录派生 ──
  // 数据驱动：切换项目后，新项目无运行记录 → 不锁定输入。
  const busy =
    activeProject?.records.some(
      (r) => r.status === 'running' || r.status === 'waiting'
    ) ?? false;

  return {
    startTranslation,
    confirmPendingTerms,
    skipPendingTerms,
    confirmDraft,
    confirmFinal,
    abort,
    activeRecordId: activeRecordIdRef.current,
    busy,
  };
}
