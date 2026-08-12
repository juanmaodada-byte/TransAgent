/**
 * QAPanel 组件
 * =============
 * 质检报告面板：总分 + 子维度分数 + 问题列表 + 总结。
 * D5 实现。
 */

import type { QAResult, QAIssue } from '../../types';
import './QAPanel.css';

export interface QAPanelProps {
  /** 质检报告 */
  qa: QAResult;
}

/** 子维度配置（权重来自 interface.py QAResult） */
const DIMENSIONS: Array<{ key: keyof QAResult; label: string; weight: number }> = [
  { key: 'term_accuracy', label: '术语准确性', weight: 30 },
  { key: 'semantic_fidelity', label: '语义忠实度', weight: 30 },
  { key: 'code_integrity', label: '代码完整性', weight: 15 },
  { key: 'fluency', label: '流畅性', weight: 15 },
  { key: 'style_match', label: '风格匹配度', weight: 10 },
];

/** 严重程度配置 */
const SEVERITY_CONFIG: Record<QAIssue['severity'], { label: string; className: string }> = {
  critical: { label: '严重', className: 'sev-critical' },
  major: { label: '主要', className: 'sev-major' },
  minor: { label: '轻微', className: 'sev-minor' },
};

/** 分数 → 颜色/等级 */
function scoreLevel(score: number): { text: string; className: string } {
  if (score >= 9) return { text: '优秀', className: 'level-excellent' };
  if (score >= 8) return { text: '良好', className: 'level-good' };
  if (score >= 7) return { text: '中等', className: 'level-medium' };
  if (score >= 6) return { text: '及格', className: 'level-pass' };
  return { text: '待改进', className: 'level-poor' };
}

export function QAPanel({ qa }: QAPanelProps) {
  const totalLevel = scoreLevel(qa.total_score);
  const hasIssues = qa.issues.length > 0;

  // 按严重程度排序：critical → major → minor
  const severityRank: Record<QAIssue['severity'], number> = { critical: 0, major: 1, minor: 2 };
  const sortedIssues = [...qa.issues].sort(
    (a, b) => severityRank[a.severity] - severityRank[b.severity]
  );

  return (
    <div className="qa-panel">
      {/* 头部：总分 */}
      <div className="qa-header">
        <div className="qa-total">
          <span className={`qa-total-score ${totalLevel.className}`}>
            {qa.total_score.toFixed(1)}
          </span>
          <span className="qa-total-label">综合评分 / 10</span>
        </div>
        <div className="qa-level-badge">
          <span className={`qa-level ${totalLevel.className}`}>{totalLevel.text}</span>
          <span className="qa-issue-count">
            {hasIssues ? `${qa.issues.length} 个问题` : '无问题 ✨'}
          </span>
        </div>
      </div>

      {/* 子维度评分条 */}
      <div className="qa-dimensions">
        {DIMENSIONS.map((dim) => {
          const value = Number(qa[dim.key]) || 0;
          return (
            <div key={dim.key} className="qa-dimension">
              <div className="qa-dim-header">
                <span className="qa-dim-label">
                  {dim.label}
                  <span className="qa-dim-weight">×{dim.weight}%</span>
                </span>
                <span className="qa-dim-value">{value.toFixed(1)}</span>
              </div>
              <div className="qa-dim-track">
                <div
                  className={`qa-dim-fill ${scoreLevel(value).className}`}
                  style={{ width: `${value * 10}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* 问题列表 */}
      {hasIssues && (
        <div className="qa-issues-list">
          <h4 className="qa-issues-title">问题列表</h4>
          <ul className="qa-issues">
            {sortedIssues.map((issue, i) => {
              const sev = SEVERITY_CONFIG[issue.severity];
              return (
                <li key={i} className={`qa-issue-item ${sev.className}`}>
                  <span className={`qa-severity ${sev.className}`}>{sev.label}</span>
                  <span className="qa-issue-type">{issue.type}</span>
                  <span className="qa-issue-desc">{issue.description}</span>
                  {issue.location && (
                    <span className="qa-issue-location">📍 {issue.location}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* 总结 */}
      {qa.summary && (
        <div className="qa-summary">
          <span className="qa-summary-icon">💡</span>
          <p>{qa.summary}</p>
        </div>
      )}
    </div>
  );
}
