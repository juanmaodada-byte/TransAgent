/**
 * MdConverterDialog 组件（D8.1 MVP）
 * =================================
 * 独立「文档转 Markdown」工具：上传文档 → 后端转换为 MD → 复制到剪贴板，
 * 用户再粘贴到主翻译输入框。主流程本身不接收文档上传。
 */

import { useState, useCallback } from 'react';
import type { DragEvent } from 'react';
import { Button, Icon, X } from '../../ui';
import { convertToMd } from '../../../api/client';
import './MdConverterDialog.css';

export interface MdConverterDialogProps {
  open: boolean;
  onClose: () => void;
}

export function MdConverterDialog({ open, onClose }: MdConverterDialogProps) {
  const [converting, setConverting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [md, setMd] = useState('');
  const [charCount, setCharCount] = useState(0);
  const [overLimit, setOverLimit] = useState(false);
  const [limit, setLimit] = useState(10000);
  const [fileName, setFileName] = useState('');
  const [copied, setCopied] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const handleFile = useCallback(async (file: File) => {
    setConverting(true);
    setError(null);
    setFileName(file.name);
    try {
      const res = await convertToMd(file);
      if (res.error) {
        setError(res.error);
        setMd('');
        return;
      }
      setMd(res.md ?? '');
      setCharCount(res.char_count ?? 0);
      setOverLimit(Boolean(res.over_limit));
      setLimit(res.limit ?? 10000);
    } catch (err) {
      setError(err instanceof Error ? err.message : '转换失败');
      setMd('');
    } finally {
      setConverting(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragOver(false);
      const f = e.dataTransfer.files?.[0];
      if (f) handleFile(f);
    },
    [handleFile]
  );

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(md);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError('复制失败，请手动选择文本复制');
    }
  }, [md]);

  if (!open) return null;

  return (
    <div className="md-converter-overlay" onClick={onClose}>
      <div
        className="md-converter-dialog"
        onClick={(e) => e.stopPropagation()}
        onDragOver={(e) => e.stopPropagation()}
        onDragEnter={(e) => e.stopPropagation()}
        onDragLeave={(e) => e.stopPropagation()}
        onDrop={(e) => e.stopPropagation()}
      >
        <div className="md-converter-header">
          <h3 className="md-converter-title">文档转 Markdown</h3>
          <Button variant="ghost" size="icon" onClick={onClose} title="关闭">
            <Icon icon={X} size={16} />
          </Button>
        </div>
        <p className="md-converter-desc">
          上传文档（md/docx/txt/pdf），转换为 Markdown 后复制，再粘贴到翻译输入框。
          转换结果不受翻译正文长度限制；超过 1 万字的建议拆分后分批翻译。
        </p>

        <label
          className={`md-converter-drop ${converting ? 'busy' : ''} ${dragOver ? 'drag-over' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            e.stopPropagation(); // 修复：阻止冒泡到 CenterPanel 触发整页拖拽遮罩
            setDragOver(true);
          }}
          onDragLeave={(e) => {
            e.stopPropagation();
            setDragOver(false);
          }}
          onDrop={handleDrop}
        >
          <input
            type="file"
            accept=".md,.docx,.doc,.pdf,.txt"
            disabled={converting}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
              e.target.value = '';
            }}
          />
          {converting ? (
            '正在转换…'
          ) : fileName ? (
            <>
              <strong>{fileName}</strong>
              <span className="md-converter-drop-sub">已选择 · 点击或重新拖入以更换</span>
            </>
          ) : (
            <>
              <strong>点击选择文档，或拖拽到此处</strong>
              <span className="md-converter-drop-sub">支持 md / docx / doc / pdf / txt</span>
            </>
          )}
        </label>

        {error && <div className="md-converter-error">{error}</div>}

        {md && (
          <div className="md-converter-result">
            <div className="md-converter-result-head">
              <span className={`md-converter-count ${overLimit ? 'over' : ''}`}>
                {charCount.toLocaleString()} 字符{overLimit ? ` · 超过输入上限 ${limit.toLocaleString()}，请拆分` : ''}
              </span>
              <Button variant="outline" size="sm" onClick={handleCopy} disabled={!md}>
                {copied ? '已复制 ✓' : '复制 Markdown'}
              </Button>
            </div>
            <textarea
              className="md-converter-textarea"
              value={md}
              readOnly
              spellCheck={false}
              placeholder="转换结果将显示在这里…"
            />
            <p className="md-converter-tip">
              复制后到下方输入框粘贴，Ctrl+Enter 开始翻译。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
