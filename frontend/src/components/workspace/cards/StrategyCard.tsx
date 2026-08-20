/**
 * StrategyCard 消息卡片
 * =====================
 * 翻译策略书展示（译前产出）。
 */

import type { StrategyBook } from '../../../types';
import './cards.css';

export interface StrategyCardProps {
  strategy: StrategyBook;
  termsSummary?: {
    total_terms: number;
    rag_hit: number;
    web_search: number;
    pending: number;
  };
}

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: '简单',
  medium: '中等',
  hard: '困难',
};

const STYLE_LABEL: Record<string, string> = {
  technical: '技术文档',
  academic: '学术论文',
  blog: '技术博客',
};

export function StrategyCard({ strategy, termsSummary }: StrategyCardProps) {
  return (
    <div className="strategy-card">
      <div className="strategy-grid">
        <div className="strategy-item">
          <span className="strategy-label">ICT 子领域</span>
          <span className="strategy-value">{strategy.ict_domain || '未知'}</span>
        </div>
        <div className="strategy-item">
          <span className="strategy-label">难度</span>
          <span className="strategy-value">
            {DIFFICULTY_LABEL[strategy.difficulty] ?? strategy.difficulty}
          </span>
        </div>
        <div className="strategy-item">
          <span className="strategy-label">风格</span>
          <span className="strategy-value">
            {STYLE_LABEL[strategy.style] ?? strategy.style}
          </span>
        </div>
        <div className="strategy-item">
          <span className="strategy-label">直译/意译</span>
          <span className="strategy-value">
            {Math.round(strategy.literal_ratio * 100)}% 直译
          </span>
        </div>
      </div>

      {termsSummary && (
        <div className="strategy-terms">
          <span className="strategy-terms-label">术语：</span>
          <span className="strategy-terms-count">{termsSummary.total_terms} 个</span>
          <span className="strategy-terms-item">RAG 命中 {termsSummary.rag_hit}</span>
          <span className="strategy-terms-item">Web 查证 {termsSummary.web_search}</span>
          {termsSummary.pending > 0 && (
            <span className="strategy-terms-item pending">
              {termsSummary.pending} 个待确认
            </span>
          )}
        </div>
      )}
    </div>
  );
}
