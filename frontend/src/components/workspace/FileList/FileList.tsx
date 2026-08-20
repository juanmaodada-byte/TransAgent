/**
 * FileList 组件
 * =============
 * 右栏文件列表：按分类分组（翻译产出 / 报告 / 知识库），行内下载。
 */

import { useCallback } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { ProjectFile, ProjectFileCategory, ProjectFileKind } from '../../../types/project';
import { downloadText } from '../../../utils/download';
import { getExportUrl } from '../../../api/client';
import { mockGetExportUrl, isMockMode } from '../../../api/mock';
import type { ExportFormat } from '../../../types';
import {
  BookOpen,
  Button,
  CheckCircle2,
  ClipboardList,
  Compass,
  Download,
  FileCode2,
  FileSearch,
  FileText,
  FolderOpen,
  Icon,
  Repeat2,
  Sparkles,
} from '../../ui';
import './FileList.css';

export interface FileListProps {
  files: ProjectFile[];
  selectedFileId: string | null;
  onSelect: (file: ProjectFile) => void;
}

/** 文件分类配置 */
const CATEGORIES: Array<{
  key: ProjectFileCategory;
  label: string;
  icon: LucideIcon;
}> = [
  { key: 'outputs', label: '翻译产出', icon: FileText },
  { key: 'reports', label: '报告', icon: ClipboardList },
  { key: 'knowledge', label: '知识库', icon: BookOpen },
];

/** 文件类型 → 图标 */
const KIND_ICONS: Record<ProjectFileKind, LucideIcon> = {
  source: FileText,
  draft: FileCode2,
  final: CheckCircle2,
  strategy: Compass,
  qa: FileSearch,
  evolution: Sparkles,
  terms: BookOpen,
  tm: Repeat2,
};

function formatTime(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export function FileList({ files, selectedFileId, onSelect }: FileListProps) {
  const handleDownload = useCallback((file: ProjectFile) => {
    // final 文件：走 ExportButton 格式（docx/html/bilingual）
    if (file.kind === 'final' && file.sessionId && file.exportFormats?.length) {
      const fmt: ExportFormat = 'docx';
      const url = isMockMode()
        ? mockGetExportUrl(file.sessionId, fmt)
        : getExportUrl(file.sessionId, fmt);
      const a = document.createElement('a');
      a.href = url;
      a.download = `translated.${fmt}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }
    // 其他文件：本地文本下载
    const ext = file.format === 'md' ? 'md' : file.format === 'json' ? 'json' : 'txt';
    downloadText(file.name.includes('.') ? file.name : `${file.name}.${ext}`, file.content);
  }, []);

  if (files.length === 0) {
    return (
      <div className="file-list-empty">
        <span className="file-list-empty-icon">
          <Icon icon={FolderOpen} size={30} />
        </span>
        <p>暂无文件</p>
        <p className="file-list-empty-hint">完成一次翻译后，产出将出现在这里</p>
      </div>
    );
  }

  return (
    <div className="file-list">
      {CATEGORIES.map((cat) => {
        const group = files.filter((f) => f.category === cat.key);
        if (group.length === 0) return null;
        return (
          <div key={cat.key} className="file-group">
            <div className="file-group-header">
              <span className="file-group-icon">
                <Icon icon={cat.icon} size={14} />
              </span>
              <span className="file-group-label">{cat.label}</span>
              <span className="file-group-count">{group.length}</span>
            </div>
            <div className="file-group-items">
              {group.map((file) => (
                <div
                  key={file.id}
                  className={`file-row ${selectedFileId === file.id ? 'active' : ''}`}
                  onClick={() => onSelect(file)}
                >
                  <span className="file-kind-icon">
                    <Icon icon={KIND_ICONS[file.kind]} size={15} />
                  </span>
                  <div className="file-row-body">
                    <span className="file-name">{file.name}</span>
                    <span className="file-meta">
                      {file.sizeKb !== undefined && file.sizeKb > 0
                        ? `${file.sizeKb} KB · `
                        : ''}
                      {formatTime(file.createdAt)}
                    </span>
                  </div>
                  <Button
                    className="file-download-btn"
                    variant="ghost"
                    size="icon"
                    title="下载"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDownload(file);
                    }}
                  >
                    <Icon icon={Download} size={14} />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
