/**
 * ProgressBar 组件
 * ================
 * 翻译进度可视化：10步流程 + 状态指示灯 + 实时消息 + 耗时 + SSE连接状态。
 * D3 实现。
 */

import { STEP_ORDER, STEP_LABELS } from '../../types';
import type { StepKey, StepState } from '../../types';
import type { ConnectionStatus } from '../../hooks/useTranslateSSE';
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
}

// ── 状态 → 图标 ──
const STATE_ICONS: Record<StepState, string> = {
  pending: '○',
  in_progress: '◉',
  completed: '✓',
  failed: '✗',
  skipped: '—',
  waiting_user: '⏳',
};

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
}: ProgressBarProps) {
  const progressPct = calcProgress(steps);
  const connCfg = CONNECTION_CONFIG[connectionStatus];

  return (
    <div className="progress-bar">
      {/* 顶部状态栏 */}
      <div className="progress-top-bar">
        <div className="progress-top-left">
          <span className="progress-pct">{progressPct}%</span>
          <span className="progress-label">翻译进度</span>
        </div>
        <div className="progress-top-right">
          <span className="progress-timer">⏱ {formatTime(elapsedSeconds)}</span>
          <span className={`connection-dot ${connCfg.dotClass}`} />
          <span className="connection-label">{connCfg.label}</span>
          {sessionId && (
            <span className="progress-session" title={sessionId}>
              #{sessionId.slice(0, 8)}
            </span>
          )}
        </div>
      </div>

      {/* 总体进度条 */}
      <div className="progress-track">
        <div
          className="progress-fill"
          style={{ width: `${progressPct}%` }}
        />
      </div>

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
          const state = steps[stepKey];
          const isActive = currentStep === stepKey;
          const label = STEP_LABELS[stepKey];

          return (
            <div
              key={stepKey}
              className={`step-row ${state} ${isActive ? 'active' : ''}`}
            >
              <div className="step-indicator">
                <span className={`step-badge ${state} ${isActive ? 'pulse' : ''}`}>
                  {STATE_ICONS[state]}
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
    </div>
  );
}
