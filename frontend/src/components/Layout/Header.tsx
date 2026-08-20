/**
 * TransAgent Header 组件
 */

import { Link } from 'react-router-dom';
import { Activity, Badge, Icon, Languages } from '../ui';
import './Header.css';

export function Header() {
  return (
    <header className="header">
      <div className="header-inner">
        <Link to="/" className="header-logo">
          <span className="header-icon">
            <Icon icon={Languages} size={20} />
          </span>
          <span className="header-title">TransAgent</span>
          <Badge variant="secondary" className="header-subtitle">ICT翻译智能体</Badge>
        </Link>
        <nav className="header-nav">
          <a
            href="/api/health"
            target="_blank"
            rel="noopener noreferrer"
            className="header-health-link"
            title="后端健康检查"
          >
            <Icon icon={Activity} size={14} />
            API状态
          </a>
        </nav>
      </div>
    </header>
  );
}
