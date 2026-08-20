/**
 * TransAgent API 客户端
 * =====================
 * 封装所有后端 API 调用。函数签名对应 server.py 的端点。
 *
 * 注意：后端使用 FastAPI Form(...)，所有 POST 请求必须用 FormData，
 * 不能用 JSON Content-Type。
 */

import type {
  UploadResponse,
  ExportFormat,
  EvolutionData,
  TermEntry,
  LLMSettings,
  LLMSettingsInput,
} from '../types';

/** API 基础地址，可通过环境变量 VITE_API_BASE_URL 覆盖 */
const BASE_URL: string =
  (import.meta as Record<string, unknown>).env?.VITE_API_BASE_URL as string ||
  'http://localhost:8000';

// ══════════════════════════════════════════════════════════════════
// POST /api/upload
// ══════════════════════════════════════════════════════════════════

/**
 * 上传文件，返回 file_id + 格式检测结果。
 * 对应 server.py upload_file()
 */
export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    throw new Error(`上传失败: HTTP ${res.status}`);
  }

  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// POST /api/confirm_terms
// ══════════════════════════════════════════════════════════════════

/**
 * 用户确认低置信度术语（唤醒暂停的翻译任务并应用确认结果）。
 * 对应 server.py confirm_terms()
 * terms 为完整 TermEntry 列表（后端用 TermEntry.from_dict 还原）。
 */
export async function confirmTerms(
  sessionId: string,
  terms: TermEntry[]
): Promise<{ accepted: boolean; count: number }> {
  const formData = new FormData();
  formData.append('session_id', sessionId);
  formData.append('confirmed_terms', JSON.stringify(terms));

  const res = await fetch(`${BASE_URL}/api/confirm_terms`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    throw new Error(`术语确认失败: HTTP ${res.status}`);
  }

  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// POST /api/confirm_draft
// ══════════════════════════════════════════════════════════════════

/**
 * 用户确认译中初译（中英对照），唤醒暂停的翻译任务继续译后。
 * 对应 server.py confirm_draft()
 */
export async function confirmDraft(sessionId: string): Promise<{ accepted: boolean }> {
  const formData = new FormData();
  formData.append('session_id', sessionId);

  const res = await fetch(`${BASE_URL}/api/confirm_draft`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    throw new Error(`初译确认失败: HTTP ${res.status}`);
  }

  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// POST /api/convert_to_md
// ══════════════════════════════════════════════════════════════════

/**
 * 独立文档转Markdown工具（D8.1 MVP）：返回可复制的 MD 文本。
 * 对应 server.py convert_to_md_api()
 */
export interface ConvertToMdResult {
  md?: string;
  char_count?: number;
  over_limit?: boolean;
  limit?: number;
  format?: string;
  warnings?: string[];
  error?: string;
}

export async function convertToMd(file: File): Promise<ConvertToMdResult> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BASE_URL}/api/convert_to_md`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    throw new Error(`文档转换失败: HTTP ${res.status}`);
  }

  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// GET /api/export/{session_id}
// ══════════════════════════════════════════════════════════════════

/**
 * 获取导出文件的下载链接。
 * 对应 server.py export()
 */
export function getExportUrl(sessionId: string, format: ExportFormat): string {
  return `${BASE_URL}/api/export/${sessionId}?format=${format}`;
}

// ══════════════════════════════════════════════════════════════════
// GET /api/evolution/{user_id}
// ══════════════════════════════════════════════════════════════════

/**
 * 获取用户进化数据。
 * 对应 server.py evolution()
 */
export async function fetchEvolution(
  userId: string
): Promise<EvolutionData> {
  const res = await fetch(`${BASE_URL}/api/evolution/${userId}`);

  if (!res.ok) {
    throw new Error(`获取进化数据失败: HTTP ${res.status}`);
  }

  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// 设置（LLM API 配置）
// ══════════════════════════════════════════════════════════════════

/**
 * 获取 LLM API 配置（脱敏）。
 * 对应 server.py get_llm_settings()
 */
export async function fetchLLMSettings(): Promise<LLMSettings> {
  const res = await fetch(`${BASE_URL}/api/settings/llm`);
  if (!res.ok) {
    throw new Error(`获取设置失败: HTTP ${res.status}`);
  }
  return res.json();
}

/**
 * 保存 LLM API 配置（api_key 留空则保留现有密钥）。
 * 对应 server.py update_llm_settings()
 */
export async function saveLLMSettings(
  payload: LLMSettingsInput
): Promise<LLMSettings> {
  const res = await fetch(`${BASE_URL}/api/settings/llm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(`保存设置失败: HTTP ${res.status}`);
  }
  return res.json();
}

// ══════════════════════════════════════════════════════════════════
// GET /api/health
// ══════════════════════════════════════════════════════════════════

/**
 * 健康检查。
 * 对应 server.py health()
 */
export async function healthCheck(): Promise<{ status: string; version: string }> {
  const res = await fetch(`${BASE_URL}/api/health`);
  if (!res.ok) {
    throw new Error(`健康检查失败: HTTP ${res.status}`);
  }
  return res.json();
}
