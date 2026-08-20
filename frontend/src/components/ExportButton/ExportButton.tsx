/**
 * ExportButton 组件
 * =================
 * 导出格式选择 + 下载。支持 md / docx / html / bilingual 四种格式。
 * D5 实现；D9 补齐 markdown 导出。
 *
 * 下拉面板用 Portal 渲染到 <body> + 视口定位：
 *   · FinalCard 有 overflow:hidden、消息区是滚动容器，普通 absolute 下拉会被裁掉
 *     （表现为「点击后没有下拉内容」）
 *   · Portal 脱离所有裁剪祖先，滚动/缩放时重定位
 *
 * 用法：
 *   <ExportButton sessionId="abc123" formats={['md', 'docx', 'html', 'bilingual']} />
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import type { LucideIcon } from 'lucide-react';
import type { ExportFormat } from '../../types';
import { getExportUrl } from '../../api/client';
import { mockGetExportUrl, isMockMode } from '../../api/mock';
import {
  Button,
  ChevronDown,
  FileCode2,
  FileDown,
  FileText,
  FileType,
  Globe2,
  Icon,
  Loader2,
} from '../ui';
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

/** 各格式下载文件名（浏览器 download 属性优先于服务端 Content-Disposition） */
const FORMAT_FILENAME: Record<ExportFormat, string> = {
  md: 'translated.md',
  docx: 'translated.docx',
  html: 'translated.html',
  bilingual: 'bilingual.docx',
};

/** 格式展示配置 */
const FORMAT_CONFIG: Record<ExportFormat, { icon: LucideIcon; label: string; hint: string }> = {
  md: {
    icon: FileCode2,
    label: 'Markdown',
    hint: '.md 纯文本，便于再编辑与版本管理',
  },
  docx: {
    icon: FileType,
    label: 'Word 文档',
    hint: '.docx 格式，适合二次编辑',
  },
  html: {
    icon: Globe2,
    label: 'HTML 网页',
    hint: '.html 格式，保留样式与高亮',
  },
  bilingual: {
    icon: FileText,
    label: '双语对照',
    hint: '源文/译文左右对照 Word 文档',
  },
};

/** 浮层定位：锚定在按钮附近 / 窄屏底部抽屉 */
type DropdownPos =
  | { kind: 'anchored'; top: number; left: number }
  | { kind: 'bottom-sheet' };

/** 浮层估算高度（4 项 + 标题 + 内边距），用于「下方空间不足时向上展开」 */
const DROPDOWN_EST_HEIGHT = 210;

export function ExportButton({
  sessionId,
  formats,
  disabled = false,
  onDownloaded,
}: ExportButtonProps) {
  const [open, setOpen] = useState(false);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const [pos, setPos] = useState<DropdownPos | null>(null);
  /** 按钮容器（浮层定位源） */
  const wrapRef = useRef<HTMLDivElement>(null);
  /** Portal 浮层（点击外部判断用） */
  const dropdownRef = useRef<HTMLDivElement>(null);

  const hasFormats = formats.length > 0;

  // 计算浮层位置：窄屏底部抽屉；否则锚定在按钮下方，空间不足时向上展开
  const updatePos = useCallback(() => {
    const el = wrapRef.current;
    if (!el) return;
    if (window.innerWidth <= 640) {
      setPos({ kind: 'bottom-sheet' });
      return;
    }
    const rect = el.getBoundingClientRect();
    const gap = 8;
    const spaceBelow = window.innerHeight - rect.bottom;
    const openBelow = spaceBelow >= DROPDOWN_EST_HEIGHT || spaceBelow >= rect.top;
    const top = openBelow
      ? rect.bottom + gap
      : Math.max(gap, rect.top - DROPDOWN_EST_HEIGHT - gap);
    setPos({ kind: 'anchored', top, left: Math.max(gap, rect.left) });
  }, []);

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
      link.download = FORMAT_FILENAME[format];
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

  // 主按钮点击：单格式直接下载；多格式切换下拉
  const handleMainClick = useCallback(() => {
    if (formats.length === 1) {
      handleDownload(formats[0]);
      return;
    }
    if (formats.length < 1) return;
    if (open) {
      setOpen(false);
    } else {
      updatePos();
      setOpen(true);
    }
  }, [formats, handleDownload, updatePos, open]);

  // 点击外部关闭（按钮自身 / Portal 浮层内部都不算外部）
  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (wrapRef.current?.contains(e.target as Node)) return;
      if (dropdownRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  // 滚动 / 缩放时重定位（消息区滚动会使按钮位移）
  useEffect(() => {
    if (!open) return;
    const onScrollOrResize = () => updatePos();
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [open, updatePos]);

  return (
    <>
      <div className="export-button" ref={wrapRef}>
        {/* 主按钮 */}
        <Button
          className="export-main-btn"
          onClick={handleMainClick}
          disabled={disabled || downloading !== null || !hasFormats}
        >
          {downloading ? (
            <>
              <Icon icon={Loader2} className="ui-icon-spin" size={15} />
              <span>导出中…</span>
            </>
          ) : (
            <>
              <Icon icon={FileDown} size={15} />
              <span>导出文档</span>
              {formats.length > 1 && <Icon icon={ChevronDown} size={15} />}
            </>
          )}
        </Button>
      </div>

      {/* 下拉面板（Portal 到 body，脱离 overflow 裁剪） */}
      {open &&
        hasFormats &&
        pos &&
        createPortal(
          <div
            className="export-dropdown"
            ref={dropdownRef}
            style={
              pos.kind === 'anchored'
                ? { top: pos.top, left: pos.left }
                : { left: 12, right: 12, bottom: 12, top: 'auto' }
            }
          >
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
                    <span className="export-option-icon">
                      <Icon icon={cfg.icon} size={18} />
                    </span>
                    <span className="export-option-body">
                      <span className="export-option-label">{cfg.label}</span>
                      <span className="export-option-hint">{cfg.hint}</span>
                    </span>
                    <span className="export-option-ext">.{FORMAT_FILENAME[format].split('.').pop()}</span>
                  </button>
                );
              })}
            </div>
          </div>,
          document.body
        )}
    </>
  );
}
