/**
 * LeftPanel 组件
 * ==============
 * 左栏：功能区（入口）+ 项目区（ProjectList）。
 */

import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useProjectsContext } from '../../../context/ProjectsContext';
import { ProjectList } from '../ProjectList/ProjectList';
import { BookOpen, FolderPlus, Icon, Repeat2, Settings } from '../../ui';
import './LeftPanel.css';

export interface LeftPanelProps {
  /** 点击项目时是否跳转工作区（true 在 WorkspacePage 内） */
  navigateOnSelect?: boolean;
  /** 功能区动作：terms(术语库)/tm(翻译记忆)；create(创建项目)/settings(设置) 在左栏内直接处理 */
  onFeature?: (key: string) => void;
  /** 打开设置视图（中栏/右栏切换，不跳独立页面） */
  onOpenSettings?: () => void;
}

/** 功能区入口配置 */
const FEATURES = [
  { key: 'create', icon: FolderPlus, label: '创建项目' },
  { key: 'terms', icon: BookOpen, label: '术语库' },
  { key: 'tm', icon: Repeat2, label: '翻译记忆' },
  { key: 'settings', icon: Settings, label: '设置' },
] as const;

export function LeftPanel({
  navigateOnSelect = true,
  onFeature,
  onOpenSettings,
}: LeftPanelProps) {
  const navigate = useNavigate();
  const { projects, activeProjectId, actions } = useProjectsContext();

  const handleCreate = useCallback(() => {
    const id = actions.createProject();
    if (navigateOnSelect) navigate(`/projects/${id}`);
  }, [actions, navigate, navigateOnSelect]);

  const handleSelect = useCallback(
    (id: string) => {
      actions.selectProject(id);
      if (navigateOnSelect) navigate(`/projects/${id}`);
    },
    [actions, navigate, navigateOnSelect]
  );

  const handleDelete = useCallback(
    (id: string) => {
      const proj = projects.find((p) => p.id === id);
      if (!window.confirm(`确定删除项目「${proj?.name ?? id}」？此操作不可撤销。`)) {
        return;
      }
      actions.deleteProject(id);
      if (activeProjectId === id) {
        navigate('/');
      }
    },
    [actions, projects, activeProjectId, navigate]
  );

  const handleFeatureClick = useCallback(
    (key: string) => {
      // 创建项目：新建并跳转到新项目初始界面
      if (key === 'create') {
        handleCreate();
        return;
      }
      // 设置：中栏/右栏切换视图（不跳独立页面）
      if (key === 'settings') {
        onOpenSettings?.();
        return;
      }
      if (onFeature) {
        onFeature(key);
      } else {
        console.warn(`[Feature] ${key} 即将上线`);
      }
    },
    [onFeature, onOpenSettings, handleCreate]
  );

  return (
    <aside className="left-panel">
      {/* ── 功能区 ── */}
      <div className="left-features">
        {FEATURES.map((f) => (
          <button
            key={f.key}
            type="button"
            className="feature-entry"
            onClick={() => handleFeatureClick(f.key)}
          >
            <span className="feature-icon">
              <Icon icon={f.icon} size={16} />
            </span>
            <span className="feature-label">{f.label}</span>
          </button>
        ))}
      </div>

      {/* ── 项目区 ── */}
      <div className="left-projects">
        <ProjectList
          projects={projects}
          activeProjectId={activeProjectId}
          onCreate={handleCreate}
          onSelect={handleSelect}
          onDelete={handleDelete}
          onRename={actions.renameProject}
        />
      </div>
    </aside>
  );
}
