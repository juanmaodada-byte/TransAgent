/**
 * TranslateViewer 组件
 * =====================
 * Markdown 富文本渲染 + 代码高亮（react-markdown + react-syntax-highlighter/Prism）。
 * D4 实现。
 *
 * 支持：标题、段落、列表、任务列表、表格、引用、分隔线、链接、图片、
 *       行内代码、代码块（Prism 高亮 + 复制按钮）。
 */

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
// 按需注册常用语言，避免打包全部 Prism 语言（减小 bundle）
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup';
import c from 'react-syntax-highlighter/dist/esm/languages/prism/c';
import cpp from 'react-syntax-highlighter/dist/esm/languages/prism/cpp';
import go from 'react-syntax-highlighter/dist/esm/languages/prism/go';
import java from 'react-syntax-highlighter/dist/esm/languages/prism/java';
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql';
import type { ComponentProps } from 'react';
import { Button, Check, Copy, Icon, Image } from '../ui';
import './TranslateViewer.css';

// 注册语言（PrismLight 需要显式注册才能高亮）
SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('javascript', javascript);
SyntaxHighlighter.registerLanguage('typescript', typescript);
SyntaxHighlighter.registerLanguage('bash', bash);
SyntaxHighlighter.registerLanguage('yaml', yaml);
SyntaxHighlighter.registerLanguage('json', json);
SyntaxHighlighter.registerLanguage('markup', markup);
SyntaxHighlighter.registerLanguage('c', c);
SyntaxHighlighter.registerLanguage('cpp', cpp);
SyntaxHighlighter.registerLanguage('go', go);
SyntaxHighlighter.registerLanguage('java', java);
SyntaxHighlighter.registerLanguage('sql', sql);

export interface TranslateViewerProps {
  /** Markdown 文本内容 */
  content: string;
  /** 额外样式类 */
  className?: string;
  /** 是否显示代码块复制按钮 */
  showCopy?: boolean;
}

/** 常见语言的 Prism 别名映射（目标必须是已注册语言） */
const LANG_MAP: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  jsx: 'javascript', // jsx 语法近似 javascript
  ts: 'typescript',
  tsx: 'typescript',
  sh: 'bash',
  shell: 'bash',
  yml: 'yaml',
  md: 'markup',
  markdown: 'markup',
  json: 'json',
  dockerfile: 'yaml',
  docker: 'yaml',
  c: 'c',
  cpp: 'cpp',
  go: 'go',
  java: 'java',
  html: 'markup',
  xml: 'markup',
  sql: 'sql',
};

/** 代码块语言标签 → 显示名 */
const LANG_DISPLAY: Record<string, string> = {
  python: 'Python',
  javascript: 'JavaScript',
  typescript: 'TypeScript',
  bash: 'Bash',
  yaml: 'YAML',
  json: 'JSON',
  markup: 'HTML',
  go: 'Go',
  java: 'Java',
  c: 'C',
  cpp: 'C++',
  sql: 'SQL',
};

/** 规范化语言标识 */
function normalizeLang(lang: string | undefined): string {
  if (!lang) return 'text';
  const key = lang.toLowerCase().trim();
  return LANG_MAP[key] || key;
}

/** 带加载失败的图片组件 */
function ViewerImage({
  src,
  alt,
}: {
  src?: string;
  alt?: string;
}) {
  const [error, setError] = useState(false);

  if (error || !src) {
    return (
      <div className="viewer-image-placeholder">
        <span>
          <Icon icon={Image} size={16} />
          图片不可用
        </span>
        {alt && <span className="viewer-image-alt">{alt}</span>}
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt || ''}
      className="viewer-image"
      onError={() => setError(true)}
      loading="lazy"
    />
  );
}

/** 代码块（带 Prism 高亮 + 复制按钮） */
function ViewerCodeBlock({
  className,
  children,
  showCopy,
  onCopy,
}: {
  className?: string;
  children?: React.ReactNode;
  showCopy: boolean;
  onCopy: (code: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const match = /language-(\w+)/.exec(className || '');
  const code = String(children ?? '').replace(/\n$/, '');

  // 无语言标识 → 当作行内代码或纯文本
  if (!match) {
    return <code className="viewer-inline-code">{children}</code>;
  }

  const lang = normalizeLang(match[1]);
  const langDisplay = LANG_DISPLAY[lang] || lang;

  return (
    <div className="viewer-code-block">
      <div className="viewer-code-header">
        <span className="viewer-code-lang">{langDisplay}</span>
        {showCopy && (
          <Button
            className="viewer-copy-btn"
            variant="ghost"
            size="sm"
            onClick={() => {
              onCopy(code);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            icon={<Icon icon={copied ? Check : Copy} size={13} />}
          >
            {copied ? '已复制' : '复制'}
          </Button>
        )}
      </div>
      <SyntaxHighlighter
        language={lang}
        style={oneDark}
        customStyle={{
          margin: 0,
          borderRadius: 0,
          borderBottomLeftRadius: '6px',
          borderBottomRightRadius: '6px',
          fontSize: '0.85rem',
          lineHeight: 1.6,
        }}
        codeTagProps={{ style: { fontFamily: 'var(--font-mono)' } }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

export function TranslateViewer({
  content,
  className = '',
  showCopy = true,
}: TranslateViewerProps) {
  const handleCopy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      // 剪贴板不可用时静默失败
    }
  };

  const components: ComponentProps<typeof ReactMarkdown>['components'] = {
    // 代码（行内 + 块）
    code: (props) => (
      <ViewerCodeBlock
        className={props.className}
        showCopy={showCopy}
        onCopy={handleCopy}
      >
        {props.children}
      </ViewerCodeBlock>
    ),

    // 标题
    h1: (props) => <h1 className="viewer-heading viewer-h1" {...props} />,
    h2: (props) => <h2 className="viewer-heading viewer-h2" {...props} />,
    h3: (props) => <h3 className="viewer-heading viewer-h3" {...props} />,
    h4: (props) => <h4 className="viewer-heading viewer-h4" {...props} />,
    h5: (props) => <h5 className="viewer-heading viewer-h5" {...props} />,
    h6: (props) => <h6 className="viewer-heading viewer-h6" {...props} />,

    // 段落与列表
    p: (props) => <p className="viewer-paragraph" {...props} />,
    ul: (props) => <ul className="viewer-list" {...props} />,
    ol: (props) => <ol className="viewer-list" {...props} />,
    li: (props) => <li className="viewer-list-item" {...props} />,

    // 表格（横向滚动容器）
    table: (props) => (
      <div className="viewer-table-wrap">
        <table className="viewer-table" {...props} />
      </div>
    ),

    // 引用 / 链接 / 图片 / 分隔线
    blockquote: (props) => <blockquote className="viewer-quote" {...props} />,
    a: (props) => (
      <a
        className="viewer-link"
        target="_blank"
        rel="noopener noreferrer"
        {...props}
      />
    ),
    img: (props) => <ViewerImage src={props.src} alt={props.alt} />,
    hr: () => <hr className="viewer-hr" />,
  };

  return (
    <div className={`translate-viewer ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
