/**
 * 文本输入工具
 * ============
 * 从 PasteInput 提取的通用逻辑：Markdown 格式自动检测。
 */

/** Markdown 特征正则（用于自动检测格式） */
const MD_PATTERNS = [
  /^#{1,6}\s/m,          // 标题
  /```/,                 // 代码块
  /^\s*[-*]\s/m,         // 无序列表
  /^\s*\d+\.\s/m,        // 有序列表
  /\|.+\|.+\|/m,         // 表格
  /!\[[^\]]*\]\(/,       // 图片
  /\[[^\]]+\]\([^)]+\)/, // 链接
  /^>\s/m,               // 引用
];

/** 自动检测文本格式（md 或 text） */
export function detectFormat(text: string): 'md' | 'text' {
  if (!text.trim()) return 'text';
  return MD_PATTERNS.some((re) => re.test(text)) ? 'md' : 'text';
}

/** 最大输入字符数（与 PasteInput 一致） */
export const MAX_INPUT_CHARS = 200_000;
