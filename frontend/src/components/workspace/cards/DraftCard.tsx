/**
 * DraftCard 消息卡片
 * ==================
 * 初译稿展示（译中产出·流式累积）。
 */

import './cards.css';

export interface DraftCardProps {
  fullText: string;
  compact?: boolean;
}

export function DraftCard({ fullText, compact = false }: DraftCardProps) {
  if (compact) {
    return (
      <div className="draft-card-compact">
        <span className="draft-compact-title">初稿已生成</span>
        <span className="draft-compact-desc">可在最终译文卡片中切换查看。</span>
      </div>
    );
  }

  return (
    <div className="draft-card">
      <pre className="draft-content">{fullText}</pre>
    </div>
  );
}
