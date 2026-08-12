/**
 * FileUpload 组件
 * ===============
 * 拖拽/点击上传文件，显示格式检测结果。
 * D2 实现。
 */

import { useState, useRef, useCallback } from 'react';
import type { DragEvent, ChangeEvent } from 'react';
import type { UploadResponse, FormatType } from '../../types';
import { uploadFile } from '../../api/client';
import { mockUploadFile, isMockMode } from '../../api/mock';
import './FileUpload.css';

export interface FileUploadProps {
  /** 上传成功回调，传入格式检测结果 */
  onUploadComplete: (result: UploadResponse) => void;
  /** 错误回调 */
  onError?: (error: string) => void;
  /** 禁用上传 */
  disabled?: boolean;
}

/** 支持的格式及对应的 accept 属性 */
const ACCEPTED_EXTENSIONS = ['.md', '.docx', '.txt'];
const ACCEPT_STRING = ACCEPTED_EXTENSIONS.join(',');

/** 最大文件大小 50MB */
const MAX_FILE_SIZE = 50 * 1024 * 1024;

/** 格式对应的图标 */
const FORMAT_ICONS: Record<FormatType, string> = {
  md: '📝',
  docx: '📄',
  pdf: '📕',
  text: '📃',
  image: '🖼️',
};

/** 格式对应的中文名 */
const FORMAT_LABELS: Record<FormatType, string> = {
  md: 'Markdown',
  docx: 'Word 文档',
  pdf: 'PDF',
  text: '纯文本',
  image: '图片',
};

export function FileUpload({
  onUploadComplete,
  onError,
  disabled = false,
}: FileUploadProps) {
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);

  // ── 文件校验 ──

  const validateFile = useCallback((file: File): string | null => {
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      return `不支持的文件格式 "${ext || '未知'}"。支持的格式：${ACCEPTED_EXTENSIONS.join('、')}`;
    }
    if (file.size > MAX_FILE_SIZE) {
      return `文件过大（${(file.size / 1024 / 1024).toFixed(0)}MB），最大支持 50MB`;
    }
    return null;
  }, []);

  // ── 上传逻辑 ──

  const handleFile = useCallback(
    async (file: File) => {
      // 清除之前的状态
      setError(null);
      setResult(null);

      // 校验
      const validationError = validateFile(file);
      if (validationError) {
        setError(validationError);
        onError?.(validationError);
        return;
      }

      // 上传
      setUploading(true);
      try {
        const uploadFn = isMockMode() ? mockUploadFile : uploadFile;
        const res = await uploadFn(file);

        if (res.error) {
          setError(res.error);
          onError?.(res.error);
        } else {
          setResult(res);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : '上传失败，请重试';
        setError(msg);
        onError?.(msg);
      } finally {
        setUploading(false);
      }
    },
    [validateFile, onError]
  );

  // ── 拖拽事件 ──

  const handleDragEnter = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!disabled && !uploading) {
        setDragActive(true);
      }
    },
    [disabled, uploading]
  );

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // 只在离开拖拽区域时取消高亮
    if (e.currentTarget === e.target) {
      setDragActive(false);
    }
  }, []);

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragActive(false);

      if (disabled || uploading) return;

      const files = e.dataTransfer.files;
      if (files.length > 0) {
        handleFile(files[0]);
      }
    },
    [disabled, uploading, handleFile]
  );

  // ── 点击选择 ──

  const handleClick = useCallback(() => {
    if (!disabled && !uploading && !result) {
      inputRef.current?.click();
    }
  }, [disabled, uploading, result]);

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        handleFile(files[0]);
      }
      // 重置 input 以便重新选择同一文件
      e.target.value = '';
    },
    [handleFile]
  );

  // ── 重新上传 ──

  const handleReset = useCallback(() => {
    setResult(null);
    setError(null);
  }, []);

  // ── 开始翻译 ──

  const handleStartTranslate = useCallback(() => {
    if (result) {
      onUploadComplete(result);
    }
  }, [result, onUploadComplete]);

  // ── 渲染：上传成功后的结果卡片 ──
  if (result && !error) {
    return (
      <div className="card upload-result">
        <div className="upload-result-header">
          <span className="upload-result-icon">
            {FORMAT_ICONS[result.format] || '📎'}
          </span>
          <div className="upload-result-info">
            <span className="upload-result-filename">{result.filename}</span>
            <span className="upload-result-meta">
              {FORMAT_LABELS[result.format] || result.format}
              {' · '}
              {result.size_kb > 1024
                ? `${(result.size_kb / 1024).toFixed(1)} MB`
                : `${result.size_kb} KB`}
              {result.page_count ? ` · ${result.page_count} 页` : ''}
            </span>
          </div>
          <span className="upload-result-check">✅</span>
        </div>

        {result.md_preview && (
          <div className="upload-result-preview">
            <pre>{result.md_preview}</pre>
          </div>
        )}

        <div className="upload-result-actions">
          <button className="btn-secondary" onClick={handleReset}>
            重新上传
          </button>
          <button className="btn-primary" onClick={handleStartTranslate}>
            🚀 开始翻译
          </button>
        </div>
      </div>
    );
  }

  // ── 渲染：上传区域 ──
  const zoneClass = [
    'upload-zone',
    dragActive && 'drag-active',
    uploading && 'uploading',
    error && 'has-error',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className="upload-container">
      <div
        className={zoneClass}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onClick={handleClick}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_STRING}
          onChange={handleChange}
          className="upload-input-hidden"
          disabled={disabled || uploading}
        />

        {uploading ? (
          <div className="upload-zone-content">
            <div className="upload-spinner" />
            <p className="upload-zone-text">正在上传并检测文件格式…</p>
            <p className="upload-zone-hint">请稍候</p>
          </div>
        ) : (
          <div className="upload-zone-content">
            <span className="upload-zone-icon">
              {error ? '⚠️' : '📁'}
            </span>
            <p className="upload-zone-text">
              {error
                ? error
                : '拖拽文件到此处，或点击选择文件'}
            </p>
            <p className="upload-zone-hint">
              支持 .md / .docx / .txt 格式，最大 50MB
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
