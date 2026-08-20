/**
 * CenterPanel 组件
 * ================
 * 中栏：对话消息流（上） + Codex 风格输入区（下）。
 * 消息分发由 ConversationMessageItem 完成（Phase 2 完整接入）。
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import type { DragEvent } from 'react';
import type { ConversationMessage, ProjectFile } from '../../../types/project';
import type { TermEntry } from '../../../types';
import { InputArea } from '../InputArea/InputArea';
import { ConversationMessageItem } from '../ConversationMessageItem/ConversationMessageItem';
import { MdConverterDialog } from '../MdConverterDialog/MdConverterDialog';
import { Icon, Paperclip, Upload } from '../../ui';
import './CenterPanel.css';

export interface CenterPanelProps {
  messages: ConversationMessage[];
  files: ProjectFile[];
  /** 当前项目 id（用于 key 重置「文档转MD」等跨项目残留状态） */
  projectId: string;
  /** 是否有运行中的翻译（busy 时输入禁用） */
  busy: boolean;
  onSendText: (text: string) => void;
  onUploadFiles: (files: File[]) => void;
  /** 中止进行中的翻译 */
  onAbort?: () => void;
  /** 术语确认提交 */
  onConfirmTerms?: (msgId: string, confirmed: TermEntry[]) => void;
  onSkipTerms?: (msgId: string) => void;
  /** 术语确认提交中 */
  confirming?: boolean;
  /** 在右栏打开文件 */
  onOpenFile?: (fileId: string) => void;
  /** 确认终稿 */
  onConfirmFinal?: (msgId: string) => void;
  /** D8.1 MVP：中英对照确认 */
  onConfirmDraft?: (msgId: string) => void;
  onSkipDraft?: (msgId: string) => void;
}

export function CenterPanel({
  messages,
  files,
  projectId,
  busy,
  onSendText,
  onAbort,
  onConfirmTerms,
  onSkipTerms,
  confirming = false,
  onOpenFile,
  onConfirmFinal,
  onConfirmDraft,
  onSkipDraft,
}: CenterPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [dragActive, setDragActive] = useState(false);
  /** D8.1 MVP：文档转 MD 工具弹窗 */
  const [converterOpen, setConverterOpen] = useState(false);
  // 用户是否贴近底部（贴底才自动滚动；向上翻阅历史时不打扰）
  const stickToBottomRef = useRef(true);

  // 新消息/心跳刷新时：仅当用户贴近底部才滚动到底
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }, []);

  const isEmpty = messages.length === 0;
  const finalRecordIds = new Set(
    messages
      .filter((msg) => msg.role === 'assistant' && msg.type === 'final' && msg.recordId)
      .map((msg) => msg.recordId)
  );
  const hiddenWhenFinal = new Set([
    'strategy',
    'draft',
    'qa',
    'evolution',
  ]);
  const visibleMessages = messages.filter((msg) => {
    if (msg.role === 'user' || !msg.recordId || !finalRecordIds.has(msg.recordId)) {
      return true;
    }
    if (msg.type === 'terms_pending') {
      return msg.status === 'waiting';
    }
    return !hiddenWhenFinal.has(msg.type);
  });

  // ── 拖拽导入 ──

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.types.includes('Files')) {
      setDragActive(true);
    }
  }, []);

  const handleDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget === e.target) {
      setDragActive(false);
    }
  }, []);

  // D8.1 MVP：主流程仅文本输入，禁用拖拽上传（文档请走「文档转MD」工具）
  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragActive(false);
    },
    []
  );

  return (
    <div
      className={`center-panel ${dragActive ? 'drag-active' : ''}`}
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* ── 消息流 ── */}
      <div className="center-messages" ref={scrollRef} onScroll={handleScroll}>
        {isEmpty ? (
          <div className="center-empty">
            <p className="center-empty-title">开始你的翻译任务</p>
            <p className="center-empty-hint">
              在下方输入要翻译的文本，或点击 <Icon icon={Paperclip} size={13} /> 上传文档
              <br />
              智能体将完成术语识别、策略制定、逐段翻译与质检润色
            </p>
          </div>
        ) : (
          visibleMessages.map((msg) => (
            <ConversationMessageItem
              key={msg.id}
              message={msg}
              messages={messages}
              files={files}
              onConfirmTerms={onConfirmTerms}
              onSkipTerms={onSkipTerms}
              confirming={confirming}
              onOpenFile={onOpenFile}
              onConfirmFinal={onConfirmFinal}
              onConfirmDraft={onConfirmDraft}
              onSkipDraft={onSkipDraft}
            />
          ))
        )}
      </div>

      {/* ── 输入区 ── */}
      <InputArea
        busy={busy}
        onSendText={onSendText}
        onOpenConverter={() => setConverterOpen(true)}
        onAbort={onAbort}
      />
      <MdConverterDialog
        key={projectId}
        open={converterOpen}
        onClose={() => {
          setConverterOpen(false);
          setDragActive(false); // 修复：关闭时清掉冒泡上来的拖拽遮罩
        }}
      />

      {/* 拖拽遮罩 */}
      {dragActive && (
        <div className="center-drop-overlay">
          <span className="center-drop-icon">
            <Icon icon={Upload} size={34} />
          </span>
          <p>松开导入文档开始翻译</p>
          <p className="center-drop-hint">支持 .md / .docx / .doc / .pdf / .txt</p>
        </div>
      )}
    </div>
  );
}
