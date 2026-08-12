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
  const supportedFormats = ['md', 'docx', 'txt'];
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

  const formatMap: Record<string, 'md' | 'docx' | 'txt'> = {
    md: 'md',
    docx: 'docx',
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
  return `/mock-export/${sessionId}.${format}`;
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
