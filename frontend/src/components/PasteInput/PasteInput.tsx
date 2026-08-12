/**
 * PasteInput 组件
 * ===============
 * 粘贴原文输入：textarea 粘贴 Markdown/纯文本 → 格式检测 → 转 File 上传翻译。
 * 新增于 D7（替代文件上传的另一种输入方式）。
 *
 * 原理：将粘贴文本包装成 Blob/File，复用 /api/upload 上传接口，后端无需任何改动。
 * 交互：上传成功后直接回调 onUploadComplete（导航到翻译页），一步到位。
 */

import { useState, useCallback, useMemo } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import type { UploadResponse } from '../../types';
import { uploadFile } from '../../api/client';
import { mockUploadFile, isMockMode } from '../../api/mock';
import './PasteInput.css';

export interface PasteInputProps {
  /** 上传成功回调，传入格式检测结果（父组件据此导航到翻译页） */
  onUploadComplete: (result: UploadResponse) => void;
  /** 错误回调 */
  onError?: (error: string) => void;
}

/** 最大输入字符数（约 50k token） */
const MAX_CHARS = 200_000;

/** Markdown 特征正则（用于自动检测格式） */
const MD_PATTERNS = [
  /^#{1,6}\s/m,          // 标题
  /```/,                 // 代码块
  /^\s*[-*]\s/m,         // 无序列表
  /^\s*\d+\.\s/m,        // 有序列表
  /\|.+\|.+\|/m,         // 表格
  /!\[[^\]]*\]\(/,       // 图片
  /\[[^\]]+\]\([^)]+\)/, // 链接
  /^>\s/m,               // 引用
];

/** 自动检测文本格式（md 或 text） */
function detectFormat(text: string): 'md' | 'text' {
  if (!text.trim()) return 'text';
  return MD_PATTERNS.some((re) => re.test(text)) ? 'md' : 'text';
}

export function PasteInput({ onUploadComplete, onError }: PasteInputProps) {
  const [text, setText] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = text.trim();
  const format = useMemo(() => detectFormat(text), [text]);
  const total = text.length;
  const isEmpty = total === 0;
  const overLimit = total > MAX_CHARS;

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    setError(null);
  }, []);

  const handleStart = useCallback(async () => {
    if (!trimmed || overLimit || uploading) {
      if (overLimit) {
        const msg = `内容超过 ${MAX_CHARS.toLocaleString()} 字符限制，请删减后重试`;
        setError(msg);
        onError?.(msg);
      }
      return;
    }

    setError(null);
    setUploading(true);

    try {
      // 将粘贴文本包装为 File，复用上传接口
      const filename = `pasted-doc.${format === 'md' ? 'md' : 'txt'}`;
      const mime = format === 'md' ? 'text/markdown' : 'text/plain';
      const file = new File([text], filename, { type: mime });

      const uploadFn = isMockMode() ? mockUploadFile : uploadFile;
      const res = await uploadFn(file);

      if (res.error) {
        setError(res.error);
        onError?.(res.error);
      } else {
        // 上传成功 → 立即导航到翻译页（一步到位）
        onUploadComplete(res);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败，请重试';
      setError(msg);
      onError?.(msg);
    } finally {
      setUploading(false);
    }
  }, [text, trimmed, format, overLimit, uploading, onError, onUploadComplete]);

  // Ctrl/Cmd + Enter 快捷键提交
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        handleStart();
      }
    },
    [handleStart]
  );

  return (
    <div className="paste-input">
      <div className={`paste-textarea-wrap ${error ? 'has-error' : ''}`}>
        <textarea
          className="paste-textarea"
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={'在此粘贴要翻译的原文（支持 Markdown / 纯文本）\n\n例如：\n# Kubernetes 入门\n\nKubernetes 是一个开源的容器编排平台...\n\n按 Ctrl+Enter 或点击下方按钮开始翻译'}
          disabled={uploading}
          maxLength={MAX_CHARS + 1000}
          spellCheck={false}
        />
        {uploading && (
          <div className="paste-uploading-overlay">
            <div className="upload-spinner" />
            <p>正在上传并检测格式…</p>
          </div>
        )}
      </div>

      {/* 底部工具栏 */}
      <div className="paste-toolbar">
        <div className="paste-stats">
          <span className={`paste-format-badge ${format}`}>
            {format === 'md' ? 'Markdown' : '纯文本'}
          </span>
          <span className="paste-char-count">
            {total.toLocaleString()} 字符
            <span className={`paste-char-limit ${overLimit ? 'over' : ''}`}>
              / {MAX_CHARS.toLocaleString()}
            </span>
          </span>
          {isEmpty && (
            <span className="paste-hint">支持 Markdown / 纯文本，Ctrl+Enter 快捷提交</span>
          )}
        </div>

        <button
          className="btn-primary paste-start-btn"
          onClick={handleStart}
          disabled={isEmpty || overLimit || uploading}
          type="button"
        >
          {uploading ? '上传中…' : '开始翻译'}
        </button>
      </div>

      {error && (
        <div className="paste-error">
          <span>⚠️</span>
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
