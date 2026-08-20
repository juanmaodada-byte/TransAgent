/**
 * ErrorCard 消息卡片
 * ==================
 * 翻译错误展示。variant="warning" 用于降级（degraded）：非致命，仅提示某步骤降级。
 */

import { AlertTriangle, Icon } from '../../ui';
import './cards.css';

export interface ErrorCardProps {
  message: string;
  variant?: 'error' | 'warning';
}

export function ErrorCard({ message, variant = 'error' }: ErrorCardProps) {
  const isWarning = variant === 'warning';
  return (
    <div className={`error-card ${isWarning ? 'warning' : ''}`}>
      <span className="error-card-icon">
        <Icon icon={AlertTriangle} size={18} />
      </span>
      <div>
        <p className="error-card-title">
          {isWarning ? '部分步骤降级' : '翻译过程出错'}
        </p>
        <p className="error-card-msg">{message}</p>
      </div>
    </div>
  );
}
