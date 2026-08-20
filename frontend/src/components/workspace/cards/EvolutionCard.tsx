/**
 * EvolutionCard 消息卡片
 * ======================
 * 进化报告展示（学习层产出）。
 */

import type { EvolutionReport } from '../../../types';
import './cards.css';

export interface EvolutionCardProps {
  evolution: EvolutionReport;
}

export function EvolutionCard({ evolution }: EvolutionCardProps) {
  return (
    <div className="evolution-card">
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
          <span className="evo-stat-value">
            {Math.round(evolution.rag_hit_rate * 100)}%
          </span>
          <span className="evo-stat-label">术语命中率</span>
        </div>
        <div className="evo-stat">
          <span className="evo-stat-value">
            {Math.round(evolution.tm_reuse_rate * 100)}%
          </span>
          <span className="evo-stat-label">TM复用率</span>
        </div>
      </div>
    </div>
  );
}
