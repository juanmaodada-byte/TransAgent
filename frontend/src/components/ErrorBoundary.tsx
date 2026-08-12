/**
 * ErrorBoundary 组件
 * ===================
 * 捕获子组件渲染错误，展示友好占位，防止整棵组件树被卸载
 * （例如 TermConfirmCard 渲染异常时，不中断翻译流程与 SSE 连接）。
 */

import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** 出错时显示的占位内容 */
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' };

  static getDerivedStateFromError(err: Error): State {
    return { hasError: true, message: err.message || String(err) };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary] 捕获渲染错误:', error, info);
  }

  handleReset = () => {
    this.setState({ hasError: false, message: '' });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="card error-boundary-fallback">
          <div className="error-boundary-icon">⚠️</div>
          <p className="error-boundary-title">该区域渲染失败</p>
          <p className="error-boundary-msg">{this.state.message}</p>
          <button className="btn-secondary" onClick={this.handleReset} type="button">
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
