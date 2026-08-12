/**
 * ExportButton 组件
 * =================
 * 导出格式选择 + 下载。支持 docx / html / bilingual 三种格式。
 * D5 实现。
 *
 * 用法：
 *   <ExportButton sessionId="abc123" formats={['docx', 'html', 'bilingual']} />
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import type { ExportFormat } from '../../types';
import { getExportUrl } from '../../api/client';
import { mockGetExportUrl, isMockMode } from '../../api/mock';
import './ExportButton.css';

export interface ExportButtonProps {
  /** 翻译会话 ID */
  sessionId: string;
  /** 可用的导出格式 */
  formats: ExportFormat[];
  /** 禁用导出 */
  disabled?: boolean;
  /** 成功触发下载后的回调 */
  onDownloaded?: (format: ExportFormat) => void;
}

/** 格式展示配置 */
const FORMAT_CONFIG: Record<ExportFormat, { icon: string; label: string; hint: string }> = {
  docx: {
    icon: '📄',
    label: 'Word 文档',
    hint: '.docx 格式，适合二次编辑',
  },
  html: {
    icon: '🌐',
    label: 'HTML 网页',
    hint: '.html 格式，保留样式与高亮',
  },
  bilingual: {
    icon: '📋',
    label: '双语对照',
    hint: '源文/译文左右对照 Markdown',
  },
};

export function ExportButton({
  sessionId,
  formats,
  disabled = false,
  onDownloaded,
}: ExportButtonProps) {
  const [open, setOpen] = useState(false);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭下拉
  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  // 选择格式触发下载
  const handleDownload = useCallback(
    (format: ExportFormat) => {
      setOpen(false);
      setDownloading(format);

      const url = isMockMode()
        ? mockGetExportUrl(sessionId, format)
        : getExportUrl(sessionId, format);

      // 创建临时 <a> 触发浏览器下载
      const link = document.createElement('a');
      link.href = url;
      link.download = `translated.${format}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // 模拟下载耗时后回调
      setTimeout(() => {
        setDownloading(null);
        onDownloaded?.(format);
      }, 600);
    },
    [sessionId, onDownloaded]
  );

  const hasFormats = formats.length > 0;

  return (
    <div className="export-button" ref={dropdownRef}>
      {/* 主按钮 */}
      <button
        className="btn-primary export-main-btn"
        onClick={() => {
          // 只有一种格式时直接下载
          if (formats.length === 1) {
            handleDownload(formats[0]);
          } else {
            setOpen((prev) => !prev);
          }
        }}
        disabled={disabled || downloading !== null || !hasFormats}
        type="button"
      >
        {downloading ? (
          <>
            <span className="export-spinner" />
            导出中…
          </>
        ) : (
          <>
            <span className="export-main-icon">📦</span>
            导出文档
            {formats.length > 1 && <span className="export-caret">▾</span>}
          </>
        )}
      </button>

      {/* 下拉面板 */}
      {open && hasFormats && (
        <div className="export-dropdown">
          <div className="export-dropdown-title">选择导出格式</div>
          <div className="export-options">
            {formats.map((format) => {
              const cfg = FORMAT_CONFIG[format];
              return (
                <button
                  key={format}
                  className="export-option"
                  onClick={() => handleDownload(format)}
                  type="button"
                >
                  <span className="export-option-icon">{cfg.icon}</span>
                  <span className="export-option-body">
                    <span className="export-option-label">{cfg.label}</span>
                    <span className="export-option-hint">{cfg.hint}</span>
                  </span>
                  <span className="export-option-ext">.{format}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
