"""
译后窗口分片（D8.1）
===================
deepseek-v4-flash 对长输入返回 HTTP200 空 content（实测 >1500字符 开始空响应）。
译前术语提取已用「包小+并行」规避（term_extraction_max_chars=1200），译后质检/润色
没有同样保护 → 长文档在译后阶段空响应风暴（每个调用重试4~5次·退避累计31s+）。

本模块提供译后技能的分片能力：把「译文 + 源文」按字符窗口切分为多个小调用，
每个调用输入回到模型稳定区。逻辑镜像 term_skill._split_fragment（段落感知·单段硬切），
不引入循环依赖（post 技能侧复用，term 技能保留自身实现）。
"""

from transagent.backend.config import get_config


def split_windows(text: str, max_chars: int | None = None) -> list[str]:
    """段落感知分片：按空行分段落，单段仍超限则空格/标点硬切。

    保证每个窗口 ≤ max_chars（除非单段本身超限且无法在边界切开）。
    与 term_skill._split_fragment 逻辑一致。
    """
    if max_chars is None:
        max_chars = getattr(get_config().pipeline, "post_segment_max_chars", 1200)
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # ① 按空行分段落（保留表格块/列表块等自然边界）
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    parts: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            parts.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        parts.append(cur)

    # ② 单段仍超限 → 硬切（优先在分隔符边界）
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        while len(p) > max_chars:
            cut = p[:max_chars]
            boundary = max(cut.rfind(" "), cut.rfind(","),
                           cut.rfind("，"), cut.rfind("。"))
            if boundary < max_chars * 0.5:
                boundary = max_chars
            final.append(p[:boundary].strip())
            p = p[boundary:].strip()
        if p:
            final.append(p)
    return final


def build_post_windows(
    draft: str,
    source_md: str,
    max_chars: int | None = None,
) -> list[tuple[str, str]]:
    """把「译文 + 源文」切分为对齐的字符窗口，供译后技能逐窗口调用。

    Returns:
        list[(draft_win, source_win)] —— 单窗口时返回原样 [(draft, source_md)]（行为不变）。

    策略：译文按段落感知分片；源文按各译文窗口字符占比等比切分（近似配对）。
    源文在润色中仅作「语义核对」、质检中 issues 摘抄由 LLM 自述，近似配对可接受。
    """
    if max_chars is None:
        max_chars = getattr(get_config().pipeline, "post_segment_max_chars", 1200)

    draft = draft or ""
    source_md = source_md or ""

    draft_wins = split_windows(draft, max_chars)
    if len(draft_wins) <= 1:
        return [(draft, source_md)]

    total = len(draft) or 1
    src_len = len(source_md)
    windows: list[tuple[str, str]] = []
    offset = 0
    for win in draft_wins:
        frac = len(win) / total
        end = min(src_len, offset + int(frac * src_len))
        # 收尾：最后一个窗口尽量取到源文末尾，避免尾部丢失
        if win is draft_wins[-1]:
            end = src_len
        windows.append((win, source_md[offset:end]))
        offset = end
    return windows
