/**
 * FinalCard 消息卡片
 * ==================
 * 完成态主结果视图：原文 / 初版译文 / 最终译文三栏对照。
 */

import { useMemo, useState } from 'react';
import type { ExportFormat } from '../../../types';
import { TranslateViewer } from '../../TranslateViewer/TranslateViewer';
import { ExportButton } from '../../ExportButton/ExportButton';
import { Button, Check, CheckCircle2, Clock3, Copy, Eye, Icon, Sparkles } from '../../ui';
import './cards.css';

export interface TripleRow {
  source_seg: string;
  draft_seg: string;
  final_seg: string;
}

export interface FinalCardProps {
  finalText: string;
  sourceText?: string;
  draftText?: string;
  sessionId?: string;
  exportFormats: ExportFormat[];
  onOpenSource?: () => void;
  onOpenDraft?: () => void;
  /** 等待用户确认终稿（确认前不导出、不沉淀） */
  awaitingConfirm?: boolean;
  /** 确认终稿回调 */
  onConfirmFinal?: () => void;
  /** 三栏句对齐（源|初译|终译）逐行 */
  alignedRows?: TripleRow[];
}

interface ComparePaneProps {
  title: string;
  meta: string;
  content: string;
  emptyText: string;
  tone?: 'final';
  onOpen?: () => void;
}

function calcTextStats(text: string) {
  const normalized = text.trim();
  return {
    chars: normalized.length,
    paragraphs: normalized ? normalized.split(/\n\s*\n/).filter(Boolean).length : 0,
  };
}

function ComparePane({
  title,
  meta,
  content,
  emptyText,
  tone,
  onOpen,
}: ComparePaneProps) {
  return (
    <section className={`compare-pane ${tone === 'final' ? 'final' : ''}`}>
      <div className="compare-pane-header">
        <div>
          <h3>{title}</h3>
          <span>{meta}</span>
        </div>
        {onOpen && (
          <Button
            className="compare-open-btn"
            variant="outline"
            size="sm"
            onClick={onOpen}
            icon={<Icon icon={Eye} size={13} />}
          >
            打开
          </Button>
        )}
      </div>
      <div className="compare-pane-body">
        {content.trim() ? (
          <TranslateViewer content={content} />
        ) : (
          <div className="compare-empty">{emptyText}</div>
        )}
      </div>
    </section>
  );
}

export function FinalCard({
  finalText,
  sourceText = '',
  draftText = '',
  sessionId,
  exportFormats,
  onOpenSource,
  onOpenDraft,
  awaitingConfirm = false,
  onConfirmFinal,
  alignedRows,
}: FinalCardProps) {
  const [copied, setCopied] = useState(false);
  const sourceStats = useMemo(() => calcTextStats(sourceText), [sourceText]);
  const draftStats = useMemo(() => calcTextStats(draftText), [draftText]);
  const finalStats = useMemo(() => calcTextStats(finalText), [finalText]);

  const handleCopyFinal = async () => {
    try {
      await navigator.clipboard.writeText(finalText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className={`final-card compare-card ${awaitingConfirm ? 'awaiting' : ''}`}>
      <div className="final-card-header">
        <div className="final-card-heading">
          <span className={`final-card-kicker ${awaitingConfirm ? 'pending' : ''}`}>
            <Icon icon={awaitingConfirm ? Clock3 : CheckCircle2} size={13} />
            {awaitingConfirm ? '待确认' : '翻译完成'}
          </span>
          <strong className="final-card-title">原文 / 初版译文 / 最终译文对照</strong>
          <span className="final-card-meta">
            最终译文 {finalStats.chars.toLocaleString()} 字符 · {finalStats.paragraphs} 段
          </span>
        </div>
        <div className="final-card-actions">
          <Button
            className="final-copy-btn"
            variant="outline"
            size="sm"
            onClick={handleCopyFinal}
            icon={<Icon icon={copied ? Check : Copy} size={13} />}
          >
            {copied ? '已复制最终译文' : '复制最终译文'}
          </Button>
        </div>
      </div>

      {/* 确认前：三栏对照（逐句对齐/整篇），供用户确认质量 */}
      {awaitingConfirm ? (
        alignedRows && alignedRows.length > 0 ? (
          <div className="triple-table-wrap">
            <table className="triple-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>原文</th>
                  <th>初版译文</th>
                  <th>最终译文</th>
                </tr>
              </thead>
              <tbody>
                {alignedRows.map((row, i) => (
                  <tr key={i}>
                    <td className="triple-idx">{i + 1}</td>
                    <td className="triple-src">{row.source_seg}</td>
                    <td className="triple-draft">{row.draft_seg || '—'}</td>
                    <td className="triple-final">{row.final_seg || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="compare-grid">
            <ComparePane
              title="原文"
              meta={`${sourceStats.chars.toLocaleString()} 字符 · ${sourceStats.paragraphs} 段`}
              content={sourceText}
              emptyText="当前任务没有可预览的原文内容。"
              onOpen={onOpenSource}
            />
            <ComparePane
              title="初版译文"
              meta={`${draftStats.chars.toLocaleString()} 字符 · ${draftStats.paragraphs} 段`}
              content={draftText}
              emptyText="初版译文尚未写入当前项目。"
              onOpen={onOpenDraft}
            />
            <ComparePane
              title="最终译文"
              meta={`${finalStats.chars.toLocaleString()} 字符 · ${finalStats.paragraphs} 段`}
              content={finalText}
              emptyText="最终译文为空。"
              tone="final"
            />
          </div>
        )
      ) : (
        /* 确认后：纯最终译文 */
        <div className="final-single-body">
          <TranslateViewer content={finalText} />
        </div>
      )}

      {/* 终稿确认区 */}
      {awaitingConfirm ? (
        <div className="final-confirm-bar">
          <div className="final-confirm-text">
            <span className="final-confirm-icon">
              <Icon icon={Sparkles} size={18} />
            </span>
            <div>
              <p className="final-confirm-title">请确认最终译文</p>
              <p className="final-confirm-desc">
                确认后将沉淀术语到知识库并解锁导出；确认前任务未完成。
              </p>
            </div>
          </div>
          <Button
            className="final-confirm-btn"
            onClick={onConfirmFinal}
            icon={<Icon icon={CheckCircle2} size={15} />}
          >
            确认并完成翻译
          </Button>
        </div>
      ) : (
        sessionId &&
        exportFormats.length > 0 && (
          <div className="final-card-footer">
            <span className="final-confirmed-note">
              <Icon icon={CheckCircle2} size={13} />
              已确认
            </span>
            <div className="final-card-export">
              <ExportButton sessionId={sessionId} formats={exportFormats} />
            </div>
          </div>
        )
      )}
    </div>
  );
}
