/**
 * ProjectList 组件
 * ================
 * 左栏项目列表：新建项目 + 项目行（名称/时间/状态）+ 删除。
 */

import { useState, useCallback } from 'react';
import type { ProjectSummary } from '../../../types/project';
import { Button, Icon, Pencil, Plus, X } from '../../ui';
import './ProjectList.css';

export interface ProjectListProps {
  projects: ProjectSummary[];
  activeProjectId: string | null;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
}

/** 状态徽章配置 */
const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  idle: { label: '', className: '' },
  running: { label: '翻译中', className: 'st-running' },
  waiting: { label: '待确认', className: 'st-waiting' },
  done: { label: '已完成', className: 'st-done' },
  error: { label: '失败', className: 'st-error' },
  aborted: { label: '已停止', className: 'st-aborted' },
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function ProjectList({
  projects,
  activeProjectId,
  onCreate,
  onSelect,
  onDelete,
  onRename,
}: ProjectListProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');

  const handleRenameStart = useCallback(
    (p: ProjectSummary) => {
      setRenamingId(p.id);
      setRenameValue(p.name);
    },
    []
  );

  const handleRenameCommit = useCallback(
    (id: string) => {
      const name = renameValue.trim();
      if (name) onRename(id, name);
      setRenamingId(null);
    },
    [renameValue, onRename]
  );

  return (
    <div className="project-list">
      <div className="project-list-header">
        <span className="project-list-title">项目</span>
        <Button
          className="project-add-btn"
          variant="ghost"
          size="icon"
          onClick={onCreate}
          title="新建项目"
        >
          <Icon icon={Plus} size={15} />
        </Button>
      </div>

      {projects.length === 0 ? (
        <div className="project-list-empty">
          暂无项目
          <br />
          点击右上角 ＋ 新建
        </div>
      ) : (
        <div className="project-list-items">
          {projects.map((p) => {
            const st = STATUS_CONFIG[p.lastRecordStatus] || STATUS_CONFIG.idle;
            const isActive = p.id === activeProjectId;
            return (
              <div
                key={p.id}
                className={`project-row ${isActive ? 'active' : ''}`}
                onClick={() => onSelect(p.id)}
              >
                <div className="project-row-main">
                  {renamingId === p.id ? (
                    <input
                      className="project-rename-input"
                      value={renameValue}
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => handleRenameCommit(p.id)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleRenameCommit(p.id);
                        if (e.key === 'Escape') setRenamingId(null);
                      }}
                    />
                  ) : (
                    <span className="project-name">{p.name}</span>
                  )}
                  {st.label && (
                    <span className={`project-status ${st.className}`}>
                      {st.label}
                    </span>
                  )}
                </div>
                <div className="project-row-meta">
                  <span className="project-meta">
                    {p.recordCount} 次翻译 · {formatTime(p.updatedAt)}
                  </span>
                  <span className="project-row-actions">
                    {!isActive && (
                      <Button
                        className="project-mini-btn"
                        variant="ghost"
                        size="icon"
                        title="重命名"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRenameStart(p);
                        }}
                      >
                        <Icon icon={Pencil} size={12} />
                      </Button>
                    )}
                    <Button
                      className="project-mini-btn danger"
                      variant="ghost"
                      size="icon"
                      title="删除项目"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(p.id);
                      }}
                    >
                      <Icon icon={X} size={13} />
                    </Button>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
