"""
文档分块器
==========
Vibe Coder B | v1.0 | 2026-08-06

职责：将受保护MD文本按章节智能切分，确保每块不超过LLM上下文窗口。
      确定性代码逻辑（非LLM），极罕见时才LLM兜底。

输入：受保护MD文本（含占位符）
输出：Chunk列表（90%+场景下只有1个chunk）

使用：
    from transagent.backend.pipeline.chunker import chunk_document
    chunks = chunk_document(protected_md, max_tokens=30000)
"""

import re
from transagent.interface import Chunk
from transagent.backend.config import get_config


def chunk_document(protected_md: str, max_tokens: int | None = None) -> list[Chunk]:
    """
    将MD文档按章节切分为chunk。

    决策树：
      1. 估算全文token → 不超窗口? 直接返回1个chunk（最常见·90%+）
      2. 超窗口 → 正则解析标题树 → 按章节切分
      3. 某章节仍超大 → 递归到子标题
      4. 无标题超长段落 → LLM找隐式边界（极罕见兜底）

    Args:
        protected_md: 结构解析器输出的受保护MD文本
        max_tokens: 单chunk最大token数，默认从配置读取

    Returns:
        Chunk列表（通常1个）
    """
    if max_tokens is None:
        max_tokens = get_config().chunk.max_tokens_per_chunk

    # 步骤1：估算全文token
    total_tokens = estimate_tokens(protected_md)

    # 步骤2：不超窗口 → 直接返回1个chunk
    if total_tokens <= max_tokens:
        return [Chunk(
            chunk_id="chunk_1",
            source_text=protected_md,
            token_estimate=total_tokens,
            heading_path=["全文"],
            order=0,
        )]

    # 步骤3：超窗口 → 按标题树切分
    chunks = _split_by_headings(protected_md, max_tokens)

    # 步骤4：仍有超长chunk → 递归到子标题/段落
    chunks = _split_oversized_chunks(chunks, max_tokens)

    return chunks


def estimate_tokens(text: str) -> int:
    """
    快速估算token数（不做实际tokenize）。

    规则：
      - 英文：word_count × 1.3
      - 中文：char_count × 0.6
      - 混合：各算各的再相加

    精度：±15%，对于"判断是否超窗口"足够。
    """
    # 统计中文字符
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    # 统计英文单词
    english_text = re.sub(r'[一-鿿]', ' ', text)
    english_words = len(english_text.split())
    # 混合估算
    return int(chinese_chars * 0.6 + english_words * 1.3)


def _split_by_headings(md_text: str, max_tokens: int) -> list[Chunk]:
    """
    按MD标题层级切分章节。
    先按##切，某章超token再按###切，以此类推。
    """
    chunks = []
    # 解析标题树
    sections = _parse_heading_tree(md_text)
    chunk_order = 0

    current_text = ""
    current_tokens = 0
    current_headings: list[str] = []

    for section in sections:
        section_text = section["text"]
        section_tokens = estimate_tokens(section_text)

        if current_tokens + section_tokens <= max_tokens:
            # 合并到当前chunk
            current_text += "\n\n" + section_text if current_text else section_text
            current_tokens += section_tokens
            if section["heading"] and section["heading"] not in current_headings:
                current_headings.append(section["heading"])
        else:
            # 当前chunk已满 → 先保存
            if current_text:
                chunks.append(Chunk(
                    chunk_id=f"chunk_{chunk_order + 1}",
                    source_text=current_text,
                    token_estimate=current_tokens,
                    heading_path=list(current_headings),
                    order=chunk_order,
                ))
                chunk_order += 1

            # 新chunk
            current_text = section_text
            current_tokens = section_tokens
            current_headings = [section["heading"]] if section["heading"] else []

    # 最后一个chunk
    if current_text:
        chunks.append(Chunk(
            chunk_id=f"chunk_{chunk_order + 1}",
            source_text=current_text,
            token_estimate=current_tokens,
            heading_path=list(current_headings),
            order=chunk_order,
        ))

    return chunks


def _parse_heading_tree(md_text: str) -> list[dict]:
    """
    解析MD标题树，返回 [{heading, level, text}, ...]。
    每个section包含该标题下的完整文本内容。
    """
    lines = md_text.split('\n')
    sections: list[dict] = []
    current_heading = ""
    current_level = 0
    current_lines: list[str] = []

    for line in lines:
        heading_match = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading_match:
            # 保存前一section
            if current_lines or current_heading:
                sections.append({
                    "heading": current_heading,
                    "level": current_level,
                    "text": '\n'.join(current_lines).strip(),
                })
            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    # 最后一个section
    sections.append({
        "heading": current_heading,
        "level": current_level,
        "text": '\n'.join(current_lines).strip(),
    })

    return sections


def _split_oversized_chunks(chunks: list[Chunk], max_tokens: int) -> list[Chunk]:
    """
    处理仍然超token的chunk——递归到子标题或段落。
    如果连子标题都没有（纯文本）→ LLM兜底或强制截断。
    """
    result = []
    for chunk in chunks:
        if chunk.token_estimate <= max_tokens:
            result.append(chunk)
        else:
            # 尝试按###子标题切分
            sub_chunks = _split_by_headings(chunk.source_text, max_tokens)
            if len(sub_chunks) > 1:
                # 重编号
                for i, sc in enumerate(sub_chunks):
                    sc.chunk_id = f"{chunk.chunk_id}_{i + 1}"
                    sc.order = chunk.order + i * 0.1
                result.extend(sub_chunks)
            else:
                # 无子标题 → 按段落强制切分（5000字一段）
                result.extend(_force_split_by_paragraphs(chunk, max_tokens))
    return result


def _force_split_by_paragraphs(chunk: Chunk, max_tokens: int) -> list[Chunk]:
    """无标题时的兜底：按段落强制分段"""
    paragraphs = chunk.source_text.split('\n\n')
    result = []
    current_text = ""
    current_tokens = 0
    sub_idx = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if current_tokens + para_tokens > max_tokens and current_text:
            sub_idx += 1
            result.append(Chunk(
                chunk_id=f"{chunk.chunk_id}_p{sub_idx}",
                source_text=current_text,
                token_estimate=current_tokens,
                heading_path=chunk.heading_path,
                order=chunk.order + sub_idx * 0.01,
            ))
            current_text = para
            current_tokens = para_tokens
        else:
            current_text += "\n\n" + para if current_text else para
            current_tokens += para_tokens

    if current_text:
        sub_idx += 1
        result.append(Chunk(
            chunk_id=f"{chunk.chunk_id}_p{sub_idx}",
            source_text=current_text,
            token_estimate=current_tokens,
            heading_path=chunk.heading_path,
            order=chunk.order + sub_idx * 0.01,
        ))

    return result
