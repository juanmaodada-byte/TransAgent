/**
 * ConversationMessageItem 组件
 * =============================
 * 对话消息分发器：按 role + type 渲染对应卡片。
 */

import type { LucideIcon } from 'lucide-react';
import type { ConversationMessage, ProjectFile } from '../../../types/project';
import type { TermEntry } from '../../../types';
import { ProgressBar } from '../../ProgressBar/ProgressBar';
import { TermConfirmCard } from '../../TermConfirmCard/TermConfirmCard';
import { DraftConfirmCard } from '../../DraftConfirmCard/DraftConfirmCard';
import { QAPanel } from '../../QAPanel/QAPanel';
import { ErrorBoundary } from '../../ErrorBoundary';
import { StrategyCard } from '../cards/StrategyCard';
import { DraftCard } from '../cards/DraftCard';
import { EvolutionCard } from '../cards/EvolutionCard';
import { ErrorCard } from '../cards/ErrorCard';
import { FinalCard } from '../cards/FinalCard';
import {
  AlertTriangle,
  Bot,
  Button,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FileCode2,
  FileText,
  FolderOpen,
  Icon,
  SkipForward,
  Sparkles,
} from '../../ui';
import './ConversationMessageItem.css';

type DraftMessage = Extract<
  ConversationMessage,
  { role: 'assistant'; type: 'draft' }
>;

export interface ConversationMessageItemProps {
  message: ConversationMessage;
  /** 当前对话流，用于关联同一次翻译的初稿和终稿 */
  messages?: ConversationMessage[];
  /** 当前项目文件，用于关联原文、初稿、终稿文件 */
  files?: ProjectFile[];
  /** 术语确认回调 */
  onConfirmTerms?: (msgId: string, confirmed: TermEntry[]) => void;
  onSkipTerms?: (msgId: string) => void;
  /** D8.1 MVP：中英对照确认回调 */
  onConfirmDraft?: (msgId: string) => void;
  onSkipDraft?: (msgId: string) => void;
  /** 确认提交中 */
  confirming?: boolean;
  /** 在右栏打开对应文件 */
  onOpenFile?: (fileId: string) => void;
  /** 确认终稿 */
  onConfirmFinal?: (msgId: string) => void;
}

const TYPE_ICON: Record<string, LucideIcon> = {
  progress: Clock3,
  strategy: ClipboardList,
  terms_pending: Clock3,
  draft_pending: Clock3,
  draft: FileCode2,
  qa: CheckCircle2,
  final: FileText,
  evolution: Sparkles,
  error: AlertTriangle,
  degraded: AlertTriangle,
};

const TYPE_TITLE: Record<string, string> = {
  progress: '翻译进度',
  strategy: '翻译策略',
  terms_pending: '术语确认',
  draft_pending: '中英对照确认',
  draft: '初译稿',
  qa: '质检报告',
  final: '翻译终稿',
  evolution: '进化报告',
  error: '错误',
  degraded: '步骤降级',
};

function isDraftMessage(message: ConversationMessage): message is DraftMessage {
  return message.role === 'assistant' && message.type === 'draft';
}

export function ConversationMessageItem({
  message,
  messages = [],
  files = [],
  onConfirmTerms,
  onSkipTerms,
  confirming = false,
  onOpenFile,
  onConfirmFinal,
  onConfirmDraft,
  onSkipDraft,
}: ConversationMessageItemProps) {
  // ── 用户消息 ──
  if (message.role === 'user') {
    const { kind, title, text } = message.data;
    return (
      <div className="cmi-row user">
        <div className="cmi-bubble user">
          <span className="cmi-user-icon">
            <Icon icon={kind === 'upload' ? FileText : ClipboardList} size={16} />
          </span>
          <div className="cmi-user-body">
            <span className="cmi-user-title">
              {kind === 'upload' ? `上传了文档：${title}` : `粘贴了文本：${title}`}
            </span>
            {text && <pre className="cmi-user-text">{text}</pre>}
          </div>
        </div>
      </div>
    );
  }

  // ── 智能体消息 ──
  const icon = TYPE_ICON[message.type] ?? Bot;
  const title = TYPE_TITLE[message.type] ?? '智能体';
  const relatedDraft = messages.find(
    (msg) =>
      isDraftMessage(msg) &&
      msg.recordId === message.recordId
  ) as DraftMessage | undefined;
  const relatedFinal = messages.find(
    (msg) =>
      msg.role === 'assistant' &&
      msg.type === 'final' &&
      msg.recordId === message.recordId
  );
  const sourceFile = files.find(
    (file) => file.kind === 'source' && file.recordId === message.recordId
  );
  const draftFile = files.find(
    (file) => file.kind === 'draft' && file.recordId === message.recordId
  );

  let body: React.ReactNode = null;

  switch (message.type) {
    case 'progress': {
      const { steps, currentStep, currentMessage, elapsedSeconds, connectionStatus, sessionId } =
        message.data;
      body = (
        <ProgressBar
          steps={steps}
          currentStep={currentStep}
          currentMessage={currentMessage}
          elapsedSeconds={elapsedSeconds}
          connectionStatus={connectionStatus}
          sessionId={sessionId}
          done={message.status === 'done'}
        />
      );
      break;
    }

    case 'strategy': {
      body = <StrategyCard strategy={message.data} termsSummary={message.data.termsSummary} />;
      break;
    }

    case 'terms_pending': {
      if (message.status === 'done') {
        const resolved = message.data.resolved;
        body = (
          <div className="cmi-terms-done">
            {resolved === 'confirmed' ? (
              <>
                <Icon icon={CheckCircle2} size={14} />
                已确认 {message.data.terms.length} 个术语，继续翻译
              </>
            ) : (
              <>
                <Icon icon={SkipForward} size={14} />
                已跳过术语确认，继续翻译
              </>
            )}
          </div>
        );
      } else {
        body = (
          <TermConfirmCard
            terms={message.data.terms}
            submitting={confirming}
            onConfirm={(confirmed) => onConfirmTerms?.(message.id, confirmed)}
            onSkip={onSkipTerms ? () => onSkipTerms(message.id) : undefined}
            timeoutSeconds={300}
          />
        );
      }
      break;
    }

    case 'draft_pending': {
      if (message.status === 'done') {
        const resolved = message.data.resolved;
        body = (
          <div className="cmi-terms-done">
            {resolved === 'confirmed' ? (
              <>
                <Icon icon={CheckCircle2} size={14} />
                已确认 {message.data.rows.length} 句初译，继续译后处理
              </>
            ) : (
              <>
                <Icon icon={SkipForward} size={14} />
                已跳过中英对照确认，继续译后处理
              </>
            )}
          </div>
        );
      } else {
        body = (
          <DraftConfirmCard
            rows={message.data.rows}
            submitting={confirming}
            onConfirm={() => onConfirmDraft?.(message.id)}
            onSkip={onSkipDraft ? () => onSkipDraft(message.id) : undefined}
            timeoutSeconds={300}
          />
        );
      }
      break;
    }

    case 'draft': {
      body = <DraftCard fullText={message.data.fullText} compact={Boolean(relatedFinal)} />;
      break;
    }

    case 'qa': {
      body = <QAPanel qa={message.data} />;
      break;
    }

    case 'final': {
      body = (
        <FinalCard
          finalText={message.data.finalText}
          sourceText={sourceFile?.content}
          draftText={draftFile?.content ?? relatedDraft?.data.fullText}
          sessionId={message.data.sessionId}
          exportFormats={message.data.exportFormats}
          onOpenSource={
            onOpenFile && message.recordId
              ? () => onOpenFile(`source-${message.recordId}`)
              : undefined
          }
          onOpenDraft={
            onOpenFile && message.recordId
              ? () => onOpenFile(`draft-${message.recordId}`)
              : undefined
          }
          awaitingConfirm={message.data.confirmed ? false : Boolean(onConfirmFinal)}
          onConfirmFinal={
            onConfirmFinal ? () => onConfirmFinal(message.id) : undefined
          }
          alignedRows={message.data.alignedRows}
        />
      );
      break;
    }

    case 'evolution': {
      body = <EvolutionCard evolution={message.data} />;
      break;
    }

    case 'error': {
      body = <ErrorCard message={message.data.message} />;
      break;
    }

    case 'degraded': {
      body = <ErrorCard message={message.data.message} variant="warning" />;
      break;
    }
  }

  // 可在右栏打开的文件（draft/final 的文件 id 可推导）
  const openableFileId =
    onOpenFile &&
    (message.type === 'draft' || message.type === 'final') &&
    message.recordId
      ? `${message.type === 'draft' ? 'draft' : 'final'}-${message.recordId}`
      : null;

  return (
    <div className="cmi-row assistant">
      <div className="cmi-avatar">
        <Icon icon={icon} size={17} />
      </div>
      <div className="cmi-content">
        <div className="cmi-type-label">
          <span className="cmi-type-title">{title}</span>
          {openableFileId && (
            <Button
              className="cmi-open-btn"
              variant="outline"
              size="sm"
              onClick={() => onOpenFile?.(openableFileId)}
              icon={<Icon icon={FolderOpen} size={13} />}
            >
              在右栏打开
            </Button>
          )}
        </div>
        <ErrorBoundary>{body}</ErrorBoundary>
      </div>
    </div>
  );
}
