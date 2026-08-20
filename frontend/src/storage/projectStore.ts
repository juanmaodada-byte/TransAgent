/**
 * 项目 localStorage 存储层
 * =========================
 * 提供项目索引与完整项目的读写、删除，以及防抖保存。
 *
 * 键设计：
 *   ta.projects.v1        → { activeProjectId, projects: ProjectSummary[] }  轻量索引
 *   ta.project.<id>.v1    → 完整 Project
 *   ta.ui.v1              → 右栏 UI 记忆（可选）
 */

import type { Project, ProjectSummary, TranslationStatus } from '../types/project';

const INDEX_KEY = 'ta.projects.v1';
const UI_KEY = 'ta.ui.v1';
const PROJECT_PREFIX = 'ta.project.';
const PROJECT_VERSION = '.v1';

export interface ProjectIndex {
  activeProjectId: string | null;
  projects: ProjectSummary[];
}

export interface UIState {
  rightPanelOpen: boolean;
  selectedFileId: string | null;
}

// ══════════════════════════════════════════════════════════════════
// 索引
// ══════════════════════════════════════════════════════════════════

export function loadIndex(): ProjectIndex {
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    if (!raw) return { activeProjectId: null, projects: [] };
    const parsed = JSON.parse(raw);
    return {
      activeProjectId: parsed.activeProjectId ?? null,
      projects: Array.isArray(parsed.projects) ? parsed.projects : [],
    };
  } catch {
    return { activeProjectId: null, projects: [] };
  }
}

export function saveIndex(index: ProjectIndex): void {
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(index));
  } catch (e) {
    console.warn('[store] 保存索引失败:', e);
  }
}

// ══════════════════════════════════════════════════════════════════
// 完整项目
// ══════════════════════════════════════════════════════════════════

export function projectKey(id: string): string {
  return `${PROJECT_PREFIX}${id}${PROJECT_VERSION}`;
}

export function loadProject(id: string): Project | null {
  try {
    const raw = localStorage.getItem(projectKey(id));
    if (!raw) return null;
    return JSON.parse(raw) as Project;
  } catch {
    return null;
  }
}

/** 同步保存完整项目 */
export function saveProject(project: Project): void {
  try {
    localStorage.setItem(projectKey(project.id), JSON.stringify(project));
  } catch (e) {
    console.warn(`[store] 保存项目 ${project.id} 失败:`, e);
  }
}

export function deleteProjectKey(id: string): void {
  try {
    localStorage.removeItem(projectKey(id));
  } catch (e) {
    console.warn(`[store] 删除项目 ${id} 失败:`, e);
  }
}

// ══════════════════════════════════════════════════════════════════
// 防抖保存（progress 高频更新时不阻塞 localStorage）
// ══════════════════════════════════════════════════════════════════

const DEBOUNCE_MS = 500;

export function createDebouncedSave(
  delay: number = DEBOUNCE_MS
): (project: Project) => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: Project | null = null;

  return (project: Project) => {
    pending = project;
    if (timer) return;
    timer = setTimeout(() => {
      if (pending) {
        saveProject(pending);
        pending = null;
      }
      timer = null;
    }, delay);
  };
}

// ══════════════════════════════════════════════════════════════════
// UI 记忆
// ══════════════════════════════════════════════════════════════════

export function loadUIState(): UIState {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (!raw) return { rightPanelOpen: false, selectedFileId: null };
    const parsed = JSON.parse(raw);
    return {
      rightPanelOpen: Boolean(parsed.rightPanelOpen),
      selectedFileId: parsed.selectedFileId ?? null,
    };
  } catch {
    return { rightPanelOpen: false, selectedFileId: null };
  }
}

export function saveUIState(state: UIState): void {
  try {
    localStorage.setItem(UI_KEY, JSON.stringify(state));
  } catch (e) {
    console.warn('[store] 保存 UI 状态失败:', e);
  }
}

// ══════════════════════════════════════════════════════════════════
// 索引派生
// ══════════════════════════════════════════════════════════════════

/** 从完整 Project 派生轻量 ProjectSummary */
export function toSummary(project: Project): ProjectSummary {
  const last = project.records[project.records.length - 1];
  return {
    id: project.id,
    name: project.name,
    updatedAt: project.updatedAt,
    recordCount: project.records.length,
    lastRecordStatus: (last?.status ?? 'idle') as TranslationStatus,
  };
}
