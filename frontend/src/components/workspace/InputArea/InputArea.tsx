/**
 * InputArea 组件
 * ==============
 * Codex 风格底部输入区：多行文本输入（主） + 📎 上传按钮 + 发送按钮。
 * 支持 Ctrl+Enter 发送；翻译进行中禁用。
 */

import { useState, useCallback, useRef } from 'react';
import type { ChangeEvent, KeyboardEvent } from 'react';
import { Button, Clock3, FileText, Icon, SendHorizontal, Square } from '../../ui';
import './InputArea.css';

export interface InputAreaProps {
  /** 忙碌（翻译进行中/等待确认）时禁用输入 */
  busy?: boolean;
  /** 发送文本（粘贴的原文） */
  onSendText: (text: string) => void;
  /** 上传文件（D8.1 MVP：主流程仅文本输入，保留类型兼容） */
  onUploadFiles?: (files: File[]) => void;
  /** D8.1 MVP：打开「文档转Markdown」工具 */
  onOpenConverter?: () => void;
  /** 中止进行中的翻译 */
  onAbort?: () => void;
  /** 占位提示 */
  placeholder?: string;
}

/** D8.1 MVP：主流程仅支持文本输入，上限与后端护栏（max_source_chars=10000）对齐 */
const MAX_CHARS = 10_000;

export function InputArea({
  busy = false,
  onSendText,
  onOpenConverter,
  onAbort,
  placeholder = '粘贴要翻译的文本（≤1万字）…（Ctrl+Enter 发送）',
}: InputAreaProps) {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const trimmed = text.trim();
  const isEmpty = trimmed.length === 0;
  const overLimit = text.length > MAX_CHARS;

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    // 自动增高（最多 ~200px）
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const handleSend = useCallback(() => {
    if (isEmpty || overLimit || busy) return;
    onSendText(trimmed);
    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [trimmed, overLimit, busy, onSendText]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Ctrl/Cmd + Enter 发送
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handleConverterClick = useCallback(() => {
    if (busy) return;
    onOpenConverter?.();
  }, [busy, onOpenConverter]);

  return (
    <div className={`input-area ${busy ? 'busy' : ''}`}>
      <div className={`input-box ${overLimit ? 'over-limit' : ''}`}>
        {/* D8.1 MVP：文档转Markdown工具入口（主流程仅文本输入） */}
        <Button
          className="input-upload-btn"
          variant="ghost"
          size="sm"
          onClick={handleConverterClick}
          disabled={busy}
          title="文档转 Markdown（转换后复制粘贴到输入框）"
        >
          <Icon icon={FileText} size={15} />
          文档转换
        </Button>

        <textarea
          ref={textareaRef}
          className="input-textarea"
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={busy ? '翻译进行中，请稍候…' : placeholder}
          disabled={busy}
          rows={1}
          spellCheck={false}
        />

        {/* 字符统计（超限时警告） */}
        {text.length > 0 && (
          <span className={`input-char-count ${overLimit ? 'over' : ''}`}>
            {text.length.toLocaleString()}
            {overLimit && ' 超限!'}
          </span>
        )}

        {/* 发送 / 停止按钮（翻译进行中变为停止键） */}
        {busy ? (
          <Button
            className="input-send-btn stop"
            variant="secondary"
            size="icon"
            onClick={onAbort}
            title="停止翻译"
          >
            <Icon icon={Square} size={14} />
          </Button>
        ) : (
          <Button
            className="input-send-btn"
            variant="default"
            size="icon"
            onClick={handleSend}
            disabled={isEmpty || overLimit}
            title="发送（Ctrl+Enter）"
          >
            <Icon icon={SendHorizontal} size={15} />
          </Button>
        )}
      </div>

      <div className="input-hint-row">
        <span className="input-hint">
          {busy ? (
            <>
              <Icon icon={Clock3} size={13} />
              翻译进行中…
            </>
          ) : (
            '粘贴文本（≤1万字）· Ctrl+Enter 发送 · 长文档请用左侧「文档转 MD」工具'
          )}
        </span>
      </div>
    </div>
  );
}
