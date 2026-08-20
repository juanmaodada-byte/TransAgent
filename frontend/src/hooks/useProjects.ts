/**
 * useProjects Hook
 * =================
 * 项目状态管理：索引 + 当前项目 + localStorage 持久化。
 * 所有修改通过 actions 完成，内部自动保存。
 */

import { useState, useCallback, useRef } from 'react';
import type {
  Project,
  ProjectSummary,
  ProjectFile,
  TranslationRecord,
  ConversationMessage,
  TranslationStatus,
} from '../types/project';
import {
  loadIndex,
  saveIndex,
  loadProject,
  deleteProjectKey,
  createDebouncedSave,
  toSummary,
  type ProjectIndex,
} from '../storage/projectStore';

/** 生成短 id */
export function genId(prefix = ''): string {
  return `${prefix}${Math.random().toString(36).slice(2, 10)}`;
}

/** 项目 actions */
export interface ProjectActions {
  createProject: (name?: string, description?: string) => string;
  selectProject: (id: string) => void;
  deleteProject: (id: string) => void;
  renameProject: (id: string, name: string) => void;
  addRecord: (record: TranslationRecord) => void;
  updateRecord: (recordId: string, patch: Partial<TranslationRecord>) => void;
  setActiveRecord: (recordId: string | undefined) => void;
  setRecordStatus: (recordId: string, status: TranslationStatus) => void;
  appendMessage: (message: ConversationMessage) => void;
  updateMessage: (messageId: string, patch: Partial<ConversationMessage>) => void;
  addFile: (file: ProjectFile) => void;
  upsertFile: (file: ProjectFile) => void;
  markUpdated: () => void;
}

export interface UseProjectsReturn {
  projects: ProjectSummary[];
  activeProjectId: string | null;
  activeProject: Project | null;
  actions: ProjectActions;
  refreshActiveProject: () => void;
}

export function useProjects(): UseProjectsReturn {
  const [index, setIndex] = useState<ProjectIndex>(() => loadIndex());
  const [activeProject, setActiveProject] = useState<Project | null>(() =>
    index.activeProjectId ? loadProject(index.activeProjectId) : null
  );

  // 防抖保存
  const debouncedSaveRef = useRef(createDebouncedSave());

  const syncIndex = useCallback((next: ProjectIndex) => {
    setIndex(next);
    saveIndex(next);
  }, []);

  /** 修改当前项目：应用 updater → 更新内存 → 防抖保存 → 更新索引摘要 */
  const updateActiveProject = useCallback(
    (updater: (p: Project) => Project) => {
      setActiveProject((prev) => {
        if (!prev) return prev;
        const next = updater(prev);
        debouncedSaveRef.current(next);
        // 同步索引摘要
        syncIndex((idx) => ({
          ...idx,
          projects: idx.projects.map((p) =>
            p.id === next.id ? toSummary(next) : p
          ),
        }));
        return next;
      });
    },
    [syncIndex]
  );

  // ── actions ──

  const createProject = useCallback(
    (name?: string, description?: string): string => {
      const id = genId('proj_');
      const now = Date.now();
      const project: Project = {
        id,
        name: name?.trim() || `新项目 ${new Date(now).toLocaleDateString()}`,
        description,
        createdAt: now,
        updatedAt: now,
        records: [],
        messages: [],
        files: [],
      };
      // 保存完整项目 + 更新索引
      debouncedSaveRef.current(project);
      syncIndex((idx) => ({
        activeProjectId: id,
        projects: [toSummary(project), ...idx.projects],
      }));
      setActiveProject(project);
      return id;
    },
    [syncIndex]
  );

  const selectProject = useCallback(
    (id: string) => {
      const proj = loadProject(id);
      setActiveProject(proj);
      syncIndex((idx) => ({ ...idx, activeProjectId: id }));
    },
    [syncIndex]
  );

  const deleteProject = useCallback(
    (id: string) => {
      deleteProjectKey(id);
      syncIndex((idx) => ({
        activeProjectId: idx.activeProjectId === id ? null : idx.activeProjectId,
        projects: idx.projects.filter((p) => p.id !== id),
      }));
      setActiveProject((prev) => (prev?.id === id ? null : prev));
    },
    [syncIndex]
  );

  const renameProject = useCallback(
    (id: string, name: string) => {
      updateActiveProject((p) =>
        p.id === id ? { ...p, name, updatedAt: Date.now() } : p
      );
    },
    [updateActiveProject]
  );

  const addRecord = useCallback(
    (record: TranslationRecord) => {
      updateActiveProject((p) => ({
        ...p,
        activeRecordId: record.id,
        updatedAt: Date.now(),
        records: [...p.records, record],
      }));
    },
    [updateActiveProject]
  );

  const updateRecord = useCallback(
    (recordId: string, patch: Partial<TranslationRecord>) => {
      updateActiveProject((p) => ({
        ...p,
        updatedAt: Date.now(),
        records: p.records.map((r) =>
          r.id === recordId ? { ...r, ...patch } : r
        ),
      }));
    },
    [updateActiveProject]
  );

  const setActiveRecord = useCallback(
    (recordId: string | undefined) => {
      updateActiveProject((p) => ({ ...p, activeRecordId: recordId }));
    },
    [updateActiveProject]
  );

  const setRecordStatus = useCallback(
    (recordId: string, status: TranslationStatus) => {
      updateActiveProject((p) => ({
        ...p,
        updatedAt: Date.now(),
        records: p.records.map((r) =>
          r.id === recordId
            ? {
                ...r,
                status,
                completedAt: status === 'done' || status === 'error'
                  ? Date.now()
                  : r.completedAt,
              }
            : r
        ),
      }));
    },
    [updateActiveProject]
  );

  const appendMessage = useCallback(
    (message: ConversationMessage) => {
      updateActiveProject((p) => ({
        ...p,
        updatedAt: Date.now(),
        messages: [...p.messages, message],
      }));
    },
    [updateActiveProject]
  );

  const updateMessage = useCallback(
    (messageId: string, patch: Partial<ConversationMessage>) => {
      updateActiveProject((p) => ({
        ...p,
        updatedAt: Date.now(),
        messages: p.messages.map((m) =>
          m.id === messageId
            ? ({ ...m, ...patch } as ConversationMessage)
            : m
        ),
      }));
    },
    [updateActiveProject]
  );

  const addFile = useCallback(
    (file: ProjectFile) => {
      updateActiveProject((p) => ({
        ...p,
        updatedAt: Date.now(),
        files: [...p.files, file],
      }));
    },
    [updateActiveProject]
  );

  const upsertFile = useCallback(
    (file: ProjectFile) => {
      updateActiveProject((p) => {
        const exists = p.files.some((f) => f.id === file.id);
        return {
          ...p,
          updatedAt: Date.now(),
          files: exists
            ? p.files.map((f) => (f.id === file.id ? file : f))
            : [...p.files, file],
        };
      });
    },
    [updateActiveProject]
  );

  const markUpdated = useCallback(() => {
    updateActiveProject((p) => ({ ...p, updatedAt: Date.now() }));
  }, [updateActiveProject]);

  /** 重新从 localStorage 加载当前项目（跨标签页/外部修改时用） */
  const refreshActiveProject = useCallback(() => {
    setActiveProject((prev) =>
      prev ? loadProject(prev.id) : prev
    );
  }, []);

  return {
    projects: index.projects,
    activeProjectId: index.activeProjectId,
    activeProject,
    actions: {
      createProject,
      selectProject,
      deleteProject,
      renameProject,
      addRecord,
      updateRecord,
      setActiveRecord,
      setRecordStatus,
      appendMessage,
      updateMessage,
      addFile,
      upsertFile,
      markUpdated,
    },
    refreshActiveProject,
  };
}
