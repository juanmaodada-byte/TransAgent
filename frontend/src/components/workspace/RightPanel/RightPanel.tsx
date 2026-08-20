/**
 * RightPanel 组件
 * ===============
 * 右栏：文件列表（上）+ 文件预览（下）。
 */

import { useCallback } from 'react';
import type { ProjectFile } from '../../../types/project';
import { FileList } from '../FileList/FileList';
import { FilePreview } from '../FilePreview/FilePreview';
import { Button, FolderOpen, Icon, X } from '../../ui';
import './RightPanel.css';

export interface RightPanelProps {
  files: ProjectFile[];
  selectedFileId: string | null;
  onSelect: (file: ProjectFile) => void;
  onClose: () => void;
}

export function RightPanel({ files, selectedFileId, onSelect, onClose }: RightPanelProps) {
  const selectedFile = files.find((f) => f.id === selectedFileId) ?? null;

  const handleSelect = useCallback(
    (file: ProjectFile) => {
      onSelect(file);
    },
    [onSelect]
  );

  return (
    <aside className="right-panel">
      <div className="right-panel-header">
        <span className="right-panel-title">
          <Icon icon={FolderOpen} size={15} />
          文件
        </span>
        <span className="right-panel-count">{files.length} 个</span>
        <Button
          className="right-panel-close"
          variant="ghost"
          size="icon"
          onClick={onClose}
          title="收起"
        >
          <Icon icon={X} size={15} />
        </Button>
      </div>

      <div className="right-panel-list">
        <FileList files={files} selectedFileId={selectedFileId} onSelect={handleSelect} />
      </div>

      <div className="right-panel-preview">
        <FilePreview file={selectedFile} />
      </div>
    </aside>
  );
}
