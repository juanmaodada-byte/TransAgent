/**
 * FilePreview 组件
 * ================
 * 右栏文件内容预览：md → TranslateViewer，text/json → 等宽 pre。
 */

import { useCallback } from 'react';
import type { ProjectFile } from '../../../types/project';
import { TranslateViewer } from '../../TranslateViewer/TranslateViewer';
import { ExportButton } from '../../ExportButton/ExportButton';
import { downloadText } from '../../../utils/download';
import { Button, Download, FileSearch, Icon } from '../../ui';
import './FilePreview.css';

export interface FilePreviewProps {
  file: ProjectFile | null;
}

export function FilePreview({ file }: FilePreviewProps) {
  const handleDownloadText = useCallback(() => {
    if (!file) return;
    const ext = file.format === 'md' ? 'md' : file.format === 'json' ? 'json' : 'txt';
    downloadText(
      file.name.includes('.') ? file.name : `${file.name}.${ext}`,
      file.content,
      file.format === 'md' ? 'text/markdown' : 'text/plain'
    );
  }, [file]);

  if (!file) {
    return (
      <div className="file-preview-empty">
        <span className="fp-empty-icon">
          <Icon icon={FileSearch} size={30} />
        </span>
        <p>从左侧选择文件查看内容</p>
      </div>
    );
  }

  return (
    <div className="file-preview">
      <div className="fp-header">
        <span className="fp-name" title={file.name}>
          {file.name}
        </span>
        {file.kind === 'final' && file.sessionId && file.exportFormats?.length ? (
          <ExportButton sessionId={file.sessionId} formats={file.exportFormats} />
        ) : (
          <Button
            className="fp-download-btn"
            variant="outline"
            onClick={handleDownloadText}
            icon={<Icon icon={Download} size={14} />}
          >
            下载
          </Button>
        )}
      </div>

      <div className="fp-body">
        {file.format === 'md' ? (
          <TranslateViewer content={file.content} />
        ) : (
          <pre className="fp-pre">{file.content || '（无内容）'}</pre>
        )}
      </div>
    </div>
  );
}
