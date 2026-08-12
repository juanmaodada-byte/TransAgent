/**
 * TranslatePage — 翻译进度与结果页
 * =================================
 * D3：集成 ProgressBar + SSE Hook + 结果展示 + 导出。
 * D4：终稿改用 TranslateViewer 渲染（Markdown + 代码高亮）。
 * D5：质检报告改用 QAPanel，导出改用 ExportButton。
 * 术语确认：集成 TermConfirmCard 完整闭环（暂停→确认→继续）。
 */

import { useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ProgressBar } from '../../components/ProgressBar/ProgressBar';
import { TranslateViewer } from '../../components/TranslateViewer/TranslateViewer';
import { QAPanel } from '../../components/QAPanel/QAPanel';
import { ExportButton } from '../../components/ExportButton/ExportButton';
import { TermConfirmCard } from '../../components/TermConfirmCard/TermConfirmCard';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import { useTranslateSSE } from '../../hooks/useTranslateSSE';
import { useMockTranslate } from '../../hooks/useMockTranslate';
import { confirmTerms } from '../../api/client';
import { isMockMode } from '../../api/mock';
import type { TermEntry } from '../../types';
import './TranslatePage.css';

/** 判断是否 Mock 模式 */
function useIsMock(): boolean {
  return isMockMode();
}

export function TranslatePage() {
  const { sessionId } = useParams<{ sessionId: string }>();

  const isMock = useIsMock();

  // 根据模式选择 hook
  const realSSE = useTranslateSSE();
  const mockSSE = useMockTranslate();
  const sse = isMock ? mockSSE : realSSE;

  // 挂载时启动 SSE。
  // 注意：不使用 startedRef 守卫——React StrictMode 会双执行 effect
  // （mount→effect→cleanup→effect），若用 ref 挡住第二次执行，
  // 第一次启动的连接会被 cleanup 的 abort() 杀掉且永不重启，
  // 页面将卡在「正在建立连接」。start() 内部幂等（会 abort 旧连接再新建），
  // 因此每次 effect 直接 start，cleanup 再 abort 是安全的。
  useEffect(() => {
    if (sessionId) {
      sse.start(sessionId);
    }
    return () => {
      sse.abort();
    };
    // 只在 sessionId 变化时触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const {
    steps,
    currentStep,
    currentMessage,
    elapsedSeconds,
    connectionStatus,
    strategy,
    termsSummary,
    pendingTerms,
    draftChunks,
    qaResult,
    finalText,
    evolution,
    exportFormats,
    error,
    realSessionId,
    clearPendingTerms,
  } = sse;

  // ── 术语确认提交状态 ──
  const [confirming, setConfirming] = useState(false);

  const isRunning =
    connectionStatus === 'connecting' || connectionStatus === 'connected';
  const isError = connectionStatus === 'error';
  const isDone = connectionStatus === 'disconnected' && finalText !== '';

  const displaySessionId = realSessionId || sessionId || '';

  // 术语确认：提交确认结果，唤醒后端翻译继续
  const handleConfirmTerms = async (confirmed: TermEntry[]) => {
    if (!displaySessionId) return;
    setConfirming(true);
    try {
      if (!isMock) {
        await confirmTerms(displaySessionId, confirmed);
      }
      // Mock 模式直接清除（无真实后端）；真实模式成功后清除
      clearPendingTerms();
    } catch (err) {
      console.error('术语确认提交失败:', err);
      // 不清除 pendingTerms，让用户可重试
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="translate-page">
      {/* 页面标题 */}
      <div className="translate-header">
        <Link to="/" className="translate-back">
          ← 返回上传
        </Link>
        <h2 className="translate-title">翻译任务</h2>
        {displaySessionId && (
          <span className="session-badge" title={displaySessionId}>
            #{displaySessionId.slice(0, 8)}
          </span>
        )}
      </div>

      {/* 初始化 / 错误 / 运行时始终显示 ProgressBar */}
      {(isRunning || isError || isDone) && (
        <ProgressBar
          steps={steps}
          currentStep={currentStep}
          currentMessage={currentMessage}
          elapsedSeconds={elapsedSeconds}
          connectionStatus={connectionStatus}
          sessionId={displaySessionId}
        />
      )}

      {/* 等待连接 */}
      {connectionStatus === 'idle' && (
        <div className="card translate-wait">
          <div className="upload-spinner" />
          <p>正在建立连接…</p>
        </div>
      )}

      {/* 错误提示 */}
      {isError && error && (
        <div className="card translate-error-card">
          <span className="error-icon">⚠️</span>
          <div>
            <p className="error-title">翻译过程出错</p>
            <p className="error-msg">{error}</p>
          </div>
        </div>
      )}

      {/* ── 翻译中：策略书 + 术语摘要 ── */}
      {isRunning && (strategy || termsSummary) && (
        <div className="card translate-info-grid">
          {strategy && (
            <div className="info-block">
              <h3 className="info-block-title">📋 翻译策略</h3>
              <dl className="info-dl">
                <dt>领域</dt>
                <dd>{strategy.ict_domain}</dd>
                <dt>难度</dt>
                <dd>{strategy.difficulty}</dd>
                <dt>风格</dt>
                <dd>{strategy.style}</dd>
                <dt>直译/意译</dt>
                <dd>{Math.round(strategy.literal_ratio * 100)}% 直译</dd>
              </dl>
            </div>
          )}
          {termsSummary && (
            <div className="info-block">
              <h3 className="info-block-title">📖 术语表</h3>
              <dl className="info-dl">
                <dt>术语总数</dt>
                <dd>{termsSummary.total_terms}</dd>
                <dt>RAG命中</dt>
                <dd>{termsSummary.rag_hit}</dd>
                <dt>待确认</dt>
                <dd>{termsSummary.pending}</dd>
              </dl>
            </div>
          )}
        </div>
      )}

      {/* 术语确认断点：翻译暂停，等待用户确认。
          用 ErrorBoundary 包裹：即使卡片渲染异常，也不中断翻译流程与 SSE 连接 */}
      {pendingTerms.length > 0 && (
        <ErrorBoundary
          fallback={
            <div className="card translate-error-card">
              <span className="error-icon">⚠️</span>
              <div>
                <p className="error-title">术语确认卡片渲染失败</p>
                <p className="error-msg">
                  翻译已暂停。请刷新页面重新上传，或稍后重试。
                </p>
              </div>
            </div>
          }
        >
          <TermConfirmCard
            terms={pendingTerms}
            submitting={confirming}
            onConfirm={handleConfirmTerms}
            onSkip={clearPendingTerms}
          />
        </ErrorBoundary>
      )}

      {/* ── 翻译中：初译稿 ── */}
      {draftChunks.length > 0 && (
        <div className="card translate-draft-card">
          <h3>📝 初译稿</h3>
          {draftChunks.map((chunk, i) => (
            <div key={i} className="draft-chunk">
              <pre>{chunk.text_chunk}</pre>
            </div>
          ))}
        </div>
      )}

      {/* ── 翻译完成：结果 ── */}
      {isDone && (
        <>
          {/* 质检报告 */}
          {qaResult && (
            <div className="card translate-qa-card">
              <h3>✅ 质检报告</h3>
              <QAPanel qa={qaResult} />
            </div>
          )}

          {/* 终稿 */}
          {finalText && (
            <div className="card translate-final-card">
              <h3>📄 翻译终稿</h3>
              <div className="final-text">
                <TranslateViewer content={finalText} />
              </div>
            </div>
          )}

          {/* 进化报告 */}
          {evolution && (
            <div className="card translate-evolution-card">
              <h3>📈 进化报告</h3>
              <p className="evolution-summary">{evolution.summary}</p>
              <div className="evolution-stats">
                <div className="evo-stat">
                  <span className="evo-stat-value">{evolution.total_terms}</span>
                  <span className="evo-stat-label">累计术语</span>
                </div>
                <div className="evo-stat">
                  <span className="evo-stat-value">{evolution.total_tm}</span>
                  <span className="evo-stat-label">累计TM</span>
                </div>
                <div className="evo-stat">
                  <span className="evo-stat-value">{Math.round(evolution.rag_hit_rate * 100)}%</span>
                  <span className="evo-stat-label">术语命中率</span>
                </div>
                <div className="evo-stat">
                  <span className="evo-stat-value">{Math.round(evolution.tm_reuse_rate * 100)}%</span>
                  <span className="evo-stat-label">TM复用率</span>
                </div>
              </div>
            </div>
          )}

          {/* 导出 */}
          {exportFormats.length > 0 && (
            <div className="card translate-export-card">
              <h3>📦 导出</h3>
              <ExportButton
                sessionId={displaySessionId}
                formats={exportFormats}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
