/**
 * ProgressBar 组件
 * ================
 * 翻译进度可视化：10步流程 + 状态指示灯 + 实时消息 + 耗时 + SSE连接状态。
 * D3 实现。
 */

import { useState } from 'react';
import { STEP_ORDER, STEP_LABELS } from '../../types';
import type { StepKey, StepState } from '../../types';
import type { ConnectionStatus } from '../../hooks/useTranslateSSE';
import { Button, Clock3, Icon, Progress, StepStateIcon } from '../ui';
import './ProgressBar.css';

export interface ProgressBarProps {
  /** 10个步骤的状态 */
  steps: Record<StepKey, StepState>;
  /** 当前正在执行的步骤 */
  currentStep: StepKey | null;
  /** 当前进度消息 */
  currentMessage: string;
  /** 已耗时（秒） */
  elapsedSeconds: number;
  /** SSE 连接状态 */
  connectionStatus: ConnectionStatus;
  /** 会话标识 */
  sessionId?: string | null;
  /** 任务是否已经结束。结束后默认收起过程细节，只保留流程节点。 */
  done?: boolean;
}

// ── 连接状态配置 ──
const CONNECTION_CONFIG: Record<ConnectionStatus, { label: string; dotClass: string }> = {
  idle: { label: '待机', dotClass: 'dot-idle' },
  connecting: { label: '连接中…', dotClass: 'dot-connecting' },
  connected: { label: '已连接', dotClass: 'dot-connected' },
  disconnected: { label: '已断开', dotClass: 'dot-disconnected' },
  error: { label: '连接错误', dotClass: 'dot-error' },
};

/** 格式化秒数为 mm:ss */
function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** 计算总体进度百分比 */
function calcProgress(steps: Record<StepKey, StepState>): number {
  const completed = STEP_ORDER.filter(
    (k) => steps[k] === 'completed' || steps[k] === 'skipped'
  ).length;
  return Math.round((completed / STEP_ORDER.length) * 100);
}

export function ProgressBar({
  steps,
  currentStep,
  currentMessage,
  elapsedSeconds,
  connectionStatus,
  sessionId,
  done = false,
}: ProgressBarProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const progressPct = calcProgress(steps);
  const connCfg = CONNECTION_CONFIG[connectionStatus];
  const compact = done && !detailsOpen;
  const completedCount = STEP_ORDER.filter(
    (k) => steps[k] === 'completed' || steps[k] === 'skipped'
  ).length;

  return (
    <div className={`progress-bar ${done ? 'done' : ''} ${compact ? 'compact' : ''}`}>
      {/* 顶部状态栏 */}
      <div className="progress-top-bar">
        <div className="progress-top-left">
          <span className="progress-pct">{done ? '完成' : `${progressPct}%`}</span>
          <span className="progress-label">
            {done ? `${completedCount}/${STEP_ORDER.length} 个流程节点已结束` : '翻译进度'}
          </span>
        </div>
        <div className="progress-top-right">
          <span className="progress-timer">
            <Icon icon={Clock3} size={14} />
            {formatTime(elapsedSeconds)}
          </span>
          {!done && (
            <>
              <span className={`connection-dot ${connCfg.dotClass}`} />
              <span className="connection-label">{connCfg.label}</span>
            </>
          )}
          {sessionId && (
            <span className="progress-session" title={sessionId}>
              #{sessionId.slice(0, 8)}
            </span>
          )}
          {done && (
            <Button
              className="progress-detail-toggle"
              variant="ghost"
              size="sm"
              onClick={() => setDetailsOpen((prev) => !prev)}
            >
              {detailsOpen ? '收起详情' : '查看详情'}
            </Button>
          )}
        </div>
      </div>

      {compact && (
        <div className="progress-flow-nodes" aria-label="翻译流程节点">
          {STEP_ORDER.map((stepKey, index) => {
            const state = steps[stepKey] ?? 'pending';
            return (
              <div key={stepKey} className={`flow-node ${state}`}>
                <span className={`flow-dot ${state}`}>
                  <StepStateIcon state={state} />
                </span>
                <span className="flow-label">{STEP_LABELS[stepKey]}</span>
                {index < STEP_ORDER.length - 1 && <span className="flow-connector" />}
              </div>
            );
          })}
        </div>
      )}

      {!compact && (
        <>
      {/* 总体进度条 */}
      <Progress value={progressPct} />

      {/* 当前消息 */}
      {currentMessage && (
        <div className="progress-current-msg">
          <span className="msg-dot" />
          {currentMessage}
        </div>
      )}

      {/* 步骤列表 */}
      <div className="progress-steps">
        {STEP_ORDER.map((stepKey, index) => {
          const state = steps[stepKey] ?? 'pending';
          const isActive = currentStep === stepKey;
          const label = STEP_LABELS[stepKey];

          return (
            <div
              key={stepKey}
              className={`step-row ${state} ${isActive ? 'active' : ''}`}
            >
              <div className="step-indicator">
                <span className={`step-badge ${state} ${isActive ? 'pulse' : ''}`}>
                  <StepStateIcon state={state} />
                </span>
                {index < STEP_ORDER.length - 1 && (
                  <div className={`step-connector ${state === 'completed' || state === 'skipped' ? 'filled' : ''}`} />
                )}
              </div>
              <div className="step-content">
                <span className="step-number">{index + 1}.</span>
                <span className="step-label">{label}</span>
                {isActive && state === 'in_progress' && (
                  <span className="step-spinner" />
                )}
              </div>
            </div>
          );
        })}
      </div>
        </>
      )}
    </div>
  );
}
