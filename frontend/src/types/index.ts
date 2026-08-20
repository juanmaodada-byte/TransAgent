/**
 * TransAgent 前端 TypeScript 类型定义
 * =====================================
 * 对应后端 transagent/interface.py 的数据契约。
 * D1 锁定，后续只能新增字段（向后兼容），不能删除/重命名。
 */

// ══════════════════════════════════════════════════════════════════
// 一、基础枚举
// ══════════════════════════════════════════════════════════════════

export type StepState =
  | 'pending'
  | 'in_progress'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'waiting_user';

export type DegradationLevel =
  | 'silent_retry'
  | 'degraded'
  | 'need_user'
  | 'meltdown';

export type Confidence = 'high' | 'medium' | 'low';

export type TermAction = 'translate' | 'notranslate';

export type TermSource =
  | 'RAG命中'
  | 'Web搜索'
  | 'LLM生成'
  | '用户确认'
  | '白名单';

export type FormatType = 'md' | 'docx' | 'doc' | 'pdf' | 'text' | 'image';

export type ExportFormat = 'md' | 'docx' | 'html' | 'bilingual';

// ══════════════════════════════════════════════════════════════════
// 二、翻译步骤定义（UI展示用）
// ══════════════════════════════════════════════════════════════════

/** 10个翻译步骤的 key，与后端 TranslationSession.steps 一致 */
export const STEP_ORDER = [
  'input_detect',
  'input_convert',
  'pre_translate',
  'terminology_confirm',
  'translate',
  'draft_confirm',
  'post_translate',
  'restore',
  'align',
  'learn',
  'export',
] as const;

export type StepKey = (typeof STEP_ORDER)[number];

/** 步骤的中文显示名 */
export const STEP_LABELS: Record<StepKey, string> = {
  input_detect: '文件检测',
  input_convert: '格式转换',
  pre_translate: '译前分析',
  terminology_confirm: '术语确认',
  translate: '翻译中',
  draft_confirm: '中英对照确认',
  post_translate: '译后处理',
  restore: '占位符还原',
  align: '句级对齐',
  learn: '知识沉淀',
  export: '导出',
};

// ══════════════════════════════════════════════════════════════════
// 三、数据契约类型
// ══════════════════════════════════════════════════════════════════

/** 文件上传响应 */
export interface UploadResponse {
  file_id: string;
  format: FormatType;
  filename: string;
  size_kb: number;
  page_count: number | null;
  md_preview: string | null;
  error?: string;
}

/** 格式检测结果 */
export interface FormatResult {
  format_type: string;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  metadata: Record<string, unknown>;
}

/** 单条术语 */
export interface TermEntry {
  term: string;
  translation: string;
  domain: string;
  confidence: Confidence;
  action: TermAction;
  source: TermSource;
  user_id: string;
  timestamp: string;
}

/** 项目术语表 */
export interface TermTable {
  entries: TermEntry[];
  pending_entries: TermEntry[];
  total_count: number;
  rag_hit_count: number;
  web_search_count: number;
  llm_gen_count: number;
}

/** 翻译记忆条目 */
export interface TMEntry {
  source_seg: string;
  target_seg: string;
  quality_score: number;
  similarity: number;
  domain: string;
  user_id: string;
  timestamp: string;
}

/** 翻译策略书 */
export interface StrategyBook {
  ict_domain: string;
  domain_confidence: string;
  difficulty: string;
  style: string;
  literal_ratio: number;
  target_audience: string;
  rules: Record<string, string>;
}

/** 质检问题 */
export interface QAIssue {
  id?: string;
  location: string;
  severity: 'critical' | 'major' | 'minor' | 'suggestion';
  nature?: 'error' | 'improvement';
  type: string;
  current?: string;
  suggestion?: string;
  description: string;
  reason?: string;
  must_fix?: boolean;
}

/** 质检报告 */
export interface QAResult {
  total_score: number;
  term_accuracy: number;
  semantic_fidelity: number;
  code_integrity: number;
  fluency: number;
  style_match: number;
  issues: QAIssue[];
  summary: string;
}

/** 进化报告 */
export interface EvolutionReport {
  new_terms_count: number;
  new_tm_count: number;
  total_terms: number;
  total_tm: number;
  tm_reuse_rate: number;
  rag_hit_rate: number;
  summary: string;
}

/** 进化数据（/api/evolution 响应） */
export interface EvolutionData {
  user_id: string;
  total_terms: number;
  total_tm: number;
  total_translations: number;
  avg_qa_score: number;
}

// ══════════════════════════════════════════════════════════════════
// 四、SSE 事件类型（译翻译流式推送）
// ══════════════════════════════════════════════════════════════════

export interface SSEProgressEvent {
  type: 'progress';
  step: StepKey;
  state: StepState;
  message: string;
  progress_pct?: number;
}

export interface SSEStrategyEvent {
  type: 'strategy';
  ict_domain: string;
  difficulty: string;
  style: string;
  literal_ratio: number;
}

export interface SSETermsEvent {
  type: 'terms';
  total_terms: number;
  rag_hit: number;
  web_search: number;
  pending: number;
  pending_terms?: TermEntry[];
}

/** 术语确认断点：翻译暂停，等待用户确认低置信度术语 */
export interface SSETermsPendingEvent {
  type: 'terms_pending';
  session_id: string;
  pending_terms: TermEntry[];
}

export interface SSEDraftEvent {
  type: 'draft';
  chunk_id: string;
  text_chunk: string;
}

export interface SSEQAEvent extends QAResult {
  type: 'qa';
}

export interface SSEFinalEvent {
  type: 'final';
  final_text: string;
  session_id: string;
  /** 三栏句对齐（源句|初译句|终译句） */
  aligned_rows?: Array<{
    source_seg: string;
    draft_seg: string;
    final_seg: string;
  }>;
}

export interface SSEEvolutionEvent extends EvolutionReport {
  type: 'evolution';
}

export interface SSEErrorEvent {
  type: 'error';
  code: string;
  message: string;
  degradation_level?: DegradationLevel;
}

export interface SSEDoneEvent {
  type: 'done';
  session_id: string;
  elapsed_seconds: number;
  export_formats: ExportFormat[];
}

/** SSE 事件联合类型 */
export type SSEEvent =
  | SSEProgressEvent
  | SSEStrategyEvent
  | SSETermsEvent
  | SSETermsPendingEvent
  | SSEDraftEvent
  | SSEQAEvent
  | SSEFinalEvent
  | SSEEvolutionEvent
  | SSEErrorEvent
  | SSEDoneEvent;

// ══════════════════════════════════════════════════════════════════
// 五、设置（LLM API 配置）
// ══════════════════════════════════════════════════════════════════

/** LLM 服务商（后端 KNOWN_PROVIDERS） */
export interface LLMProvider {
  id: string;
  label: string;
  default_base_url: string;
  models: string[];
}

/** 单个 LLM 通道（脱敏视图） */
export interface LLMChannel {
  provider: string;
  model: string;
  has_key: boolean;
  key_masked: string;
  base_url: string;
}

/** GET /api/settings/llm 响应 */
export interface LLMSettings {
  providers: LLMProvider[];
  primary: LLMChannel;
  backup: LLMChannel;
}

/** POST /api/settings/llm 请求体 */
export interface LLMSettingsInput {
  primary: {
    provider: string;
    model: string;
    api_key: string;
    base_url: string;
  };
  backup: {
    provider: string;
    model: string;
    api_key: string;
    base_url: string;
  };
}
