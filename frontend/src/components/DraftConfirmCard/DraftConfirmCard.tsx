/**
 * DraftConfirmCard 组件（D8.1 MVP）
 * ================================
 * 译中完成后的中英对照确认卡片：翻译在此暂停，用户核对「原文 ↔ 初译」逐句对照。
 * 确认后提交到 /api/confirm_draft，唤醒翻译继续译后。
 *
 * 完整闭环：
 *   SSE draft_pending 事件 → 本卡片 → 用户确认 → confirmDraft API → 翻译恢复
 */

import { useState, useEffect, useCallback } from 'react';
import { Badge, Button, Check, Clock3, Icon } from '../ui';
import './DraftConfirmCard.css';

export interface DraftConfirmRow {
  source_seg: string;
  target_seg: string;
  chunk_id?: string;
}

export interface DraftConfirmCardProps {
  /** 中英对照行（源 ↔ 初译） */
  rows: DraftConfirmRow[];
  /** 提交中状态 */
  submitting?: boolean;
  /** 确认回调 */
  onConfirm: () => void;
  /** 跳过确认（不提交，后端超时会自动继续） */
  onSkip?: () => void;
  /** 确认超时（秒） */
  timeoutSeconds?: number;
}

export function DraftConfirmCard({
  rows,
  submitting = false,
  onConfirm,
  onSkip,
  timeoutSeconds = 300,
}: DraftConfirmCardProps) {
  const [secondsLeft, setSecondsLeft] = useState(timeoutSeconds);

  useEffect(() => {
    setSecondsLeft(timeoutSeconds);
    const timer = setInterval(() => {
      setSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [timeoutSeconds, rows]);

  const handleConfirm = useCallback(() => {
    if (!submitting) onConfirm();
  }, [onConfirm, submitting]);

  return (
    <div className="draft-confirm-card">
      <div className="draft-confirm-header">
        <div className="draft-confirm-title-row">
          <span className="draft-confirm-icon">
            <Icon icon={Clock3} size={18} />
          </span>
          <h3 className="draft-confirm-title">请核对中英对照</h3>
          <Badge variant="warning" className="draft-confirm-badge">
            {rows.length} 句待确认
          </Badge>
        </div>
        <p className="draft-confirm-desc">
          初译已完成，翻译已暂停。请核对原文与初译的逐句对照，确认后继续译后处理。
        </p>
      </div>

      {/* 中英两栏逐句对照 */}
      <div className="draft-confirm-table-wrap">
        <table className="draft-confirm-table">
          <thead>
            <tr>
              <th>#</th>
              <th>原文</th>
              <th>初译</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td className="draft-confirm-idx">{i + 1}</td>
                <td className="draft-confirm-src">{row.source_seg || '—'}</td>
                <td className="draft-confirm-tgt">{row.target_seg || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 底部操作 */}
      <div className="draft-confirm-footer">
        <span className="draft-confirm-tip">
          {secondsLeft <= 60 ? (
            <span className="draft-confirm-countdown urgent">
              <Icon icon={Clock3} size={14} /> {secondsLeft} 秒后未确认将自动继续译后处理
            </span>
          ) : (
            <span className="draft-confirm-countdown">
              （{Math.ceil(secondsLeft / 60)} 分钟内未确认将自动继续）
            </span>
          )}
        </span>
        <div className="draft-confirm-buttons">
          {onSkip && (
            <Button variant="outline" onClick={onSkip} disabled={submitting}>
              跳过
            </Button>
          )}
          <Button
            onClick={handleConfirm}
            disabled={submitting}
            icon={!submitting ? <Icon icon={Check} size={15} /> : undefined}
          >
            {submitting ? '提交中…' : '确认并继续译后'}
          </Button>
        </div>
      </div>
    </div>
  );
}
