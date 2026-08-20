/**
 * TransAgent Mock 客户端
 * ======================
 * 提供与 client.ts 相同签名的 mock 实现，支持前端独立开发。
 * 开启方式：设置环境变量 VITE_USE_MOCK=true
 */

import type {
  UploadResponse,
  EvolutionData,
  TermAction,
  LLMSettings,
  LLMSettingsInput,
} from '../types';

/** 模拟网络延迟（毫秒） */
const MOCK_DELAY = 800;

function delay(ms: number = MOCK_DELAY): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ══════════════════════════════════════════════════════════════════
// Mock: uploadFile
// ══════════════════════════════════════════════════════════════════

export async function mockUploadFile(file: File): Promise<UploadResponse> {
  await delay();

  const ext = file.name.split('.').pop()?.toLowerCase();

  // 格式校验
  const supportedFormats = ['md', 'docx', 'doc', 'pdf', 'txt'];
  if (!ext || !supportedFormats.includes(ext)) {
    return {
      file_id: '',
      format: 'text' as const,
      filename: file.name,
      size_kb: Math.round(file.size / 1024 * 10) / 10,
      page_count: null,
      md_preview: null,
      error: `不支持的文件格式: .${ext || '未知'}`,
    };
  }

  const formatMap: Record<string, 'md' | 'docx' | 'doc' | 'pdf' | 'text'> = {
    md: 'md',
    docx: 'docx',
    doc: 'doc',
    pdf: 'pdf',
    txt: 'text',
  };

  const fileId = generateFileId();

  return {
    file_id: fileId,
    format: formatMap[ext],
    filename: file.name,
    size_kb: Math.round(file.size / 1024 * 10) / 10,
    page_count: ext === 'docx' ? 12 : null,
    md_preview: ext === 'md'
      ? '# 文档标题\n\n这是前500字符的预览内容...\n\n```python\nprint("hello world")\n```'
      : null,
  };
}

// ══════════════════════════════════════════════════════════════════
// Mock: confirmTerms
// ══════════════════════════════════════════════════════════════════

export async function mockConfirmTerms(
  _sessionId: string,
  terms: Array<{ term: string; translation: string; action: TermAction }>
): Promise<{ accepted: boolean; count: number }> {
  await delay(400);
  return { accepted: true, count: terms.length };
}

// ══════════════════════════════════════════════════════════════════
// Mock: getExportUrl
// ══════════════════════════════════════════════════════════════════

export function mockGetExportUrl(sessionId: string, format: string): string {
  const ext = format === 'bilingual' ? 'docx' : format;
  return `/mock-export/${sessionId}.${ext}`;
}

// ══════════════════════════════════════════════════════════════════
// Mock: fetchEvolution
// ══════════════════════════════════════════════════════════════════

export async function mockFetchEvolution(
  _userId: string
): Promise<EvolutionData> {
  await delay(500);
  return {
    user_id: _userId,
    total_terms: 156,
    total_tm: 820,
    total_translations: 23,
    avg_qa_score: 9.1,
  };
}

// ══════════════════════════════════════════════════════════════════
// Mock: healthCheck
// ══════════════════════════════════════════════════════════════════

export async function mockHealthCheck(): Promise<{ status: string; version: string }> {
  await delay(200);
  return { status: 'ok', version: '1.0.0-mock' };
}

// ══════════════════════════════════════════════════════════════════
// Mock: 设置（LLM API 配置）
// ══════════════════════════════════════════════════════════════════

const MOCK_SETTINGS_KEY = 'ta.settings.llm.v1';

const MOCK_PROVIDERS = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    default_base_url: 'https://api.deepseek.com/v1',
    models: ['deepseek-v4-flash', 'deepseek-v4-pro'],
  },
  {
    id: 'qwen',
    label: '通义千问',
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    models: ['qwen-plus', 'qwen-max', 'qwen-turbo'],
  },
  {
    id: 'zhipu',
    label: '智谱 GLM',
    default_base_url: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['glm-4-plus', 'glm-4-flash'],
  },
];

function mockMaskKey(key: string): string {
  if (!key) return '';
  if (key.length <= 8) return '***';
  return `${key.slice(0, 3)}****${key.slice(-4)}`;
}

/** 本地持久化结构：含原始 api_key（mock 仅本地，可接受） */
interface MockStoredChannel {
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
}

interface MockStored {
  primary: MockStoredChannel;
  backup: MockStoredChannel;
}

function mockLoadStored(): MockStored {
  try {
    const raw = localStorage.getItem(MOCK_SETTINGS_KEY);
    if (raw) return JSON.parse(raw) as MockStored;
  } catch {
    /* 忽略损坏数据 */
  }
  return {
    primary: { provider: 'deepseek', model: 'deepseek-v4-flash', base_url: 'https://api.deepseek.com/v1', api_key: '' },
    backup: { provider: 'qwen', model: 'qwen-plus', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', api_key: '' },
  };
}

function toChannelView(ch: MockStoredChannel): LLMSettings['primary'] {
  return {
    provider: ch.provider,
    model: ch.model,
    base_url: ch.base_url,
    has_key: Boolean(ch.api_key),
    key_masked: mockMaskKey(ch.api_key),
  };
}

export async function mockFetchLLMSettings(): Promise<LLMSettings> {
  await delay(300);
  const stored = mockLoadStored();
  return {
    providers: MOCK_PROVIDERS,
    primary: toChannelView(stored.primary),
    backup: toChannelView(stored.backup),
  };
}

export async function mockSaveLLMSettings(payload: LLMSettingsInput): Promise<LLMSettings> {
  await delay(500);
  const prev = mockLoadStored();
  // api_key 留空 → 保留现有密钥
  const next: MockStored = {
    primary: {
      provider: payload.primary.provider || prev.primary.provider,
      model: payload.primary.model || prev.primary.model,
      base_url: payload.primary.base_url || prev.primary.base_url,
      api_key: payload.primary.api_key || prev.primary.api_key,
    },
    backup: {
      provider: payload.backup.provider || prev.backup.provider,
      model: payload.backup.model || prev.backup.model,
      base_url: payload.backup.base_url || prev.backup.base_url,
      api_key: payload.backup.api_key || prev.backup.api_key,
    },
  };
  try {
    localStorage.setItem(MOCK_SETTINGS_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  return {
    providers: MOCK_PROVIDERS,
    primary: toChannelView(next.primary),
    backup: toChannelView(next.backup),
  };
}

// ══════════════════════════════════════════════════════════════════
// 辅助
// ══════════════════════════════════════════════════════════════════

function generateFileId(): string {
  return Math.random().toString(36).substring(2, 10);
}

/**
 * 判断是否使用 Mock 模式。
 * 在前端独立开发时，设置 VITE_USE_MOCK=true 即可绕过真实后端。
 */
export function isMockMode(): boolean {
  return (
    (import.meta as Record<string, unknown>).env?.VITE_USE_MOCK as string
  ) === 'true';
}
