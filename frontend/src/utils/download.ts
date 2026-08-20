/**
 * 下载工具
 * =========
 * 前端生成文本文件并触发浏览器下载（Blob + <a download>）。
 */

export function downloadText(
  filename: string,
  content: string,
  mime = 'text/plain'
): void {
  try {
    const blob = new Blob([content], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    console.warn(`[download] 下载 ${filename} 失败:`, e);
  }
}
