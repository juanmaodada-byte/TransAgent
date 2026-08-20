/**
 * 前端本地项目模型
 * =================
 * 三栏工作区使用的前端本地数据模型（独立于后端镜像类型 types/index.ts）。
 * 项目/对话消息/项目文件全部落在 localStorage（前端先行，后端接口后续补）。
 */

import type {
  StepKey,
  StepState,
  TermEntry,
  StrategyBook,
  QAResult,
  EvolutionReport,
  ExportFormat,
} from './index';
import type { ConnectionStatus } from '../hooks/useTranslateSSE';

// ══════════════════════════════════════════════════════════════════
// 一、项目
// ══════════════════════════════════════════════════════════════════

export type TranslationStatus =
  | 'idle'
  | 'running'
  | 'waiting'
  | 'done'
  | 'error'
  | 'aborted';

/** 左栏项目列表项（轻量索引） */
export interface ProjectSummary {
  id: string;
  name: string;
  updatedAt: number;
  recordCount: number;
  lastRecordStatus: TranslationStatus;
}

/** 一次翻译运行 */
export interface TranslationRecord {
  id: string;               // = fileId
  title: string;            // 源文件名 或 粘贴标题
  inputKind: 'file' | 'paste';
  fileId: string;           // 后端 file_id
  realSessionId: string | null;
  status: TranslationStatus;
  createdAt: number;
  completedAt?: number;
  elapsedSeconds?: number;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  createdAt: number;
  updatedAt: number;
  activeRecordId?: string;
  records: TranslationRecord[];
  /** 整个项目的对话流（跨多次翻译） */
  messages: ConversationMessage[];
  /** 整个项目的文件（右栏） */
  files: ProjectFile[];
}

// ══════════════════════════════════════════════════════════════════
// 二、对话消息（可辨识联合）
// ══════════════════════════════════════════════════════════════════

export interface UserMessageData {
  kind: 'upload' | 'paste';
  title: string;
  text?: string;
  fileId?: string;
}

/** ProgressMessageData 直接复用 ProgressBarProps 的形状 */
export interface ProgressMessageData {
  steps: Record<StepKey, StepState>;
  currentStep: StepKey | null;
  currentMessage: string;
  elapsedSeconds: number;
  connectionStatus: ConnectionStatus;
  sessionId?: string | null;
}

export type ConversationMessage =
  | {
      id: string;
      role: 'user';
      recordId?: string;
      createdAt: number;
      data: UserMessageData;
    }
  | {
      id: string;
      role: 'assistant';
      type: 'progress';
      recordId?: string;
      createdAt: number;
      status: 'running' | 'done' | 'error';
      data: ProgressMessageData;
    }
  | {
      id: string;
      role: 'assistant';
      type: 'strategy';
      recordId?: string;
      createdAt: number;
      data: StrategyBook & {
        termsSummary?: {
          total_terms: number;
          rag_hit: number;
          web_search: number;
          pending: number;
        };
      };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'terms_pending';
      recordId?: string;
      createdAt: number;
      status: 'waiting' | 'done';
      data: {
        terms: TermEntry[];
        sessionId?: string;
        resolved?: 'confirmed' | 'skipped' | null;
      };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'draft_pending';
      recordId?: string;
      createdAt: number;
      status: 'waiting' | 'done';
      data: {
        rows: Array<{ source_seg: string; target_seg: string; chunk_id?: string }>;
        sessionId?: string;
        resolved?: 'confirmed' | 'skipped' | null;
      };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'draft';
      recordId?: string;
      createdAt: number;
      data: {
        chunks: Array<{ chunk_id: string; text_chunk: string }>;
        fullText: string;
      };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'qa';
      recordId?: string;
      createdAt: number;
      data: QAResult;
    }
  | {
      id: string;
      role: 'assistant';
      type: 'final';
      recordId?: string;
      createdAt: number;
      status?: 'waiting' | 'done';
      data: {
        finalText: string;
        sessionId?: string;
        exportFormats: ExportFormat[];
        /** 用户是否已确认终稿（确认后才解锁导出/沉淀） */
        confirmed?: boolean;
        /** 三栏句对齐（源句|初译句|终译句） */
        alignedRows?: Array<{
          source_seg: string;
          draft_seg: string;
          final_seg: string;
        }>;
      };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'evolution';
      recordId?: string;
      createdAt: number;
      data: EvolutionReport;
    }
  | {
      id: string;
      role: 'assistant';
      type: 'error';
      recordId?: string;
      createdAt: number;
      data: { message: string };
    }
  | {
      id: string;
      role: 'assistant';
      type: 'degraded';
      recordId?: string;
      createdAt: number;
      data: { message: string };
    };

// ══════════════════════════════════════════════════════════════════
// 三、项目文件（右栏）
// ══════════════════════════════════════════════════════════════════

export type ProjectFileCategory = 'outputs' | 'reports' | 'knowledge';

export type ProjectFileKind =
  | 'source'
  | 'draft'
  | 'final'
  | 'strategy'
  | 'qa'
  | 'evolution'
  | 'terms'
  | 'tm';

export interface ProjectFile {
  id: string;
  kind: ProjectFileKind;
  category: ProjectFileCategory;
  name: string;
  /** 可预览文本（md/text/json） */
  content: string;
  format: 'md' | 'text' | 'json';
  sizeKb?: number;
  createdAt: number;
  recordId?: string;
  /** final 文件下载导出时用 */
  sessionId?: string;
  /** final 文件可导出格式 */
  exportFormats?: ExportFormat[];
}
