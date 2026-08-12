/**
 * UploadPage — 输入页
 * ===================
 * D2：集成 FileUpload 组件，上传成功后导航到翻译页。
 * D7：增加「粘贴文本」输入方式，支持 tab 切换。
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileUpload } from '../../components/FileUpload/FileUpload';
import { PasteInput } from '../../components/PasteInput/PasteInput';
import type { UploadResponse } from '../../types';
import './UploadPage.css';

type InputMode = 'file' | 'paste';

export function UploadPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<InputMode>('file');

  const handleUploadComplete = (result: UploadResponse) => {
    // 上传成功 → 跳转到翻译页
    navigate(`/translate/${result.file_id}`);
  };

  const handleError = (error: string) => {
    console.error('输入内容处理失败:', error);
  };

  return (
    <div className="upload-page">
      <div className="upload-hero">
        <h1 className="upload-title">ICT 文档智能翻译</h1>
        <p className="upload-desc">
          上传文档或粘贴原文，AI 智能体将自动完成术语识别、策略制定、逐段翻译和质检润色。
        </p>
      </div>

      {/* 输入方式切换 */}
      <div className="input-mode-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'file'}
          className={`input-mode-tab ${mode === 'file' ? 'active' : ''}`}
          onClick={() => setMode('file')}
        >
          <span className="tab-icon">📄</span>
          文件上传
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === 'paste'}
          className={`input-mode-tab ${mode === 'paste' ? 'active' : ''}`}
          onClick={() => setMode('paste')}
        >
          <span className="tab-icon">📋</span>
          粘贴文本
        </button>
      </div>

      {/* 输入面板 */}
      <div className="input-mode-panel">
        {mode === 'file' ? (
          <FileUpload
            onUploadComplete={handleUploadComplete}
            onError={handleError}
          />
        ) : (
          <PasteInput
            onUploadComplete={handleUploadComplete}
            onError={handleError}
          />
        )}
      </div>

      <div className="upload-features">
        <div className="feature-item">
          <span className="feature-icon">🔍</span>
          <span>ICT术语自动识别</span>
        </div>
        <div className="feature-item">
          <span className="feature-icon">📝</span>
          <span>代码块保护翻译</span>
        </div>
        <div className="feature-item">
          <span className="feature-icon">✅</span>
          <span>智能质检润色</span>
        </div>
      </div>
    </div>
  );
}
