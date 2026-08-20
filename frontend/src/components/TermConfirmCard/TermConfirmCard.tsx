/**
 * TermConfirmCard 组件
 * =====================
 * 低置信度术语确认卡片：翻译在此暂停，用户逐条确认/修改译法或设为不译。
 * 确认后提交到 /api/confirm_terms，唤醒翻译继续。
 *
 * 完整闭环（前后端配合）：
 *   SSE terms_pending 事件 → 本卡片 → 用户确认 → confirmTerms API → 翻译恢复
 */

import { useState, useCallback, useMemo, useEffect } from 'react';
import type { TermEntry, TermAction } from '../../types';
import { ArrowRight, Badge, Button, Check, Clock3, Icon, SourceIcon } from '../ui';
import './TermConfirmCard.css';

export interface TermConfirmCardProps {
  /** 待确认的术语列表 */
  terms: TermEntry[];
  /** 提交中状态 */
  submitting?: boolean;
  /** 确认回调：返回用户确认后的术语列表 */
  onConfirm: (confirmed: TermEntry[]) => void;
  /** 跳过确认（不提交，后端超时会自动接受） */
  onSkip?: () => void;
  /** 确认超时（秒）：超时后后端自动接受并继续 */
  timeoutSeconds?: number;
}

/** 单条术语的编辑状态 */
interface EditState {
  translation: string;
  action: TermAction;
}

/** 术语来源 → 样式类 */
function sourceClass(source: string): string {
  switch (source) {
    case 'RAG命中':
      return 'src-rag';
    case 'Web搜索':
      return 'src-web';
    case 'LLM生成':
      return 'src-llm';
    case '用户确认':
      return 'src-user';
    case '白名单':
      return 'src-whitelist';
    default:
      return 'src-other';
  }
}

export function TermConfirmCard({
  terms,
  submitting = false,
  onConfirm,
  onSkip,
  timeoutSeconds = 300,
}: TermConfirmCardProps) {
  // 确认超时倒计时（提醒用户及时操作）
  const [secondsLeft, setSecondsLeft] = useState(timeoutSeconds);

  useEffect(() => {
    setSecondsLeft(timeoutSeconds);
    const timer = setInterval(() => {
      setSecondsLeft((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [timeoutSeconds, terms]);
  // 初始化编辑状态（index → 可编辑内容）
  const [edits, setEdits] = useState<Record<number, EditState>>(() => {
    const init: Record<number, EditState> = {};
    terms.forEach((t, i) => {
      init[i] = { translation: t.translation, action: t.action };
    });
    return init;
  });

  const updateEdit = useCallback((index: number, patch: Partial<EditState>) => {
    setEdits((prev) => ({
      ...prev,
      [index]: { ...(prev[index] || {}), ...patch },
    }));
  }, []);

  // 是否所有术语都"翻译"（有 notranslate 的会影响统计）
  const allTranslated = useMemo(
    () => Object.values(edits).every((e) => e.action === 'translate'),
    [edits]
  );

  // ── 确认提交 ──
  const handleConfirm = useCallback(() => {
    const confirmed: TermEntry[] = terms.map((t, i) => {
      const edit = edits[i] || { translation: t.translation, action: t.action };
      // 不译 → 保留原文作为"译文"
      const translation = edit.action === 'notranslate' ? t.term : edit.translation;
      return {
        ...t,
        translation,
        action: edit.action,
        confidence: 'high' as const,   // 用户确认后升为高置信度
        source: '用户确认',
      };
    });
    onConfirm(confirmed);
  }, [terms, edits, onConfirm]);

  return (
    <div className="term-confirm-card">
      <div className="term-confirm-header">
        <div className="term-confirm-title-row">
          <span className="term-confirm-icon">
            <Icon icon={Clock3} size={18} />
          </span>
          <h3 className="term-confirm-title">请确认术语译法</h3>
          <Badge variant="warning" className="term-confirm-badge">
            {terms.length} 个待确认
          </Badge>
        </div>
        <p className="term-confirm-desc">
          以下为本次提取的全部术语，翻译已暂停。请确认或修改译法，确认后继续翻译。
        </p>
      </div>

      {/* 术语编辑列表 */}
      <div className="term-confirm-list">
        {terms.map((term, i) => {
          const edit = edits[i] || { translation: term.translation, action: term.action };
          const isNotranslate = edit.action === 'notranslate';
          return (
            <div
              key={`${term.term}-${i}`}
              className={`term-edit-row ${isNotranslate ? 'notranslate' : ''}`}
            >
              <div className="term-edit-source">
                <span className="term-edit-word">{term.term}</span>
                <div className="term-edit-meta">
                  {term.source && (
                    <span className={`term-source-badge ${sourceClass(term.source)}`}>
                      <SourceIcon source={term.source} />
                      {term.source}
                    </span>
                  )}
                  {term.domain && (
                    <span className="term-edit-domain">{term.domain}</span>
                  )}
                </div>
              </div>

              <div className="term-edit-arrow">
                <Icon icon={ArrowRight} size={16} />
              </div>

              <div className="term-edit-target">
                <input
                  className="term-edit-input"
                  value={edit.translation}
                  onChange={(e) =>
                    updateEdit(i, { translation: e.target.value })
                  }
                  disabled={isNotranslate || submitting}
                  placeholder="输入译文…"
                />
              </div>

              {/* 操作切换 */}
              <div className="term-edit-actions">
                <label className={`term-action-toggle ${!isNotranslate ? 'active' : ''}`}>
                  <input
                    type="radio"
                    name={`action-${i}`}
                    checked={!isNotranslate}
                    onChange={() => updateEdit(i, { action: 'translate' })}
                    disabled={submitting}
                  />
                  翻译
                </label>
                <label className={`term-action-toggle ${isNotranslate ? 'active' : ''}`}>
                  <input
                    type="radio"
                    name={`action-${i}`}
                    checked={isNotranslate}
                    onChange={() => updateEdit(i, { action: 'notranslate' })}
                    disabled={submitting}
                  />
                  保留原文
                </label>
              </div>
            </div>
          );
        })}
      </div>

      {/* 底部操作 */}
      <div className="term-confirm-footer">
        <span className="term-confirm-tip">
          {secondsLeft <= 60 ? (
            <span className="term-confirm-countdown urgent">
              <Icon icon={Clock3} size={14} /> {secondsLeft} 秒后未确认将自动接受并继续翻译
            </span>
          ) : (
            <>
              {allTranslated
                ? '确认后将以高置信度写入术语库'
                : '标为「保留原文」的术语将不翻译'}
              <span className="term-confirm-countdown">
                （{Math.ceil(secondsLeft / 60)} 分钟内未确认将自动接受）
              </span>
            </>
          )}
        </span>
        <div className="term-confirm-buttons">
          {onSkip && (
            <Button
              variant="outline"
              onClick={onSkip}
              disabled={submitting}
            >
              跳过
            </Button>
          )}
          <Button
            onClick={handleConfirm}
            disabled={submitting}
            icon={!submitting ? <Icon icon={Check} size={15} /> : undefined}
          >
            {submitting ? '提交中…' : '确认并继续翻译'}
          </Button>
        </div>
      </div>
    </div>
  );
}
