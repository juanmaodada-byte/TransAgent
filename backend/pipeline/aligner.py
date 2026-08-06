"""
句级对齐
========
Vibe Coder B | v1.0 | 2026-08-06

职责：源文↔译文句级自动对齐。
      确定性NLP算法（标点+长度+关键词匹配），不经过LLM。
      产出AlignedPair列表，用于TM写入和双语对照导出。

使用：
    from transagent.backend.pipeline.aligner import align_sentences
    pairs = align_sentences(source_md, target_md)
"""

import re
from transagent.interface import AlignedPair


def align_sentences(source_md: str, target_md: str) -> list[AlignedPair]:
    """
    源文↔译文句级对齐。

    算法：
      1. 分割源文和译文为句子列表
      2. 按句子顺序对齐（假设翻译基本保持句序）
      3. 用长度比 + 关键词重叠率计算对齐置信度

    Args:
        source_md: 源文MD文本
        target_md: 译文MD文本

    Returns:
        AlignedPair列表
    """
    source_sents = _split_sentences(source_md)
    target_sents = _split_sentences(target_md)

    pairs: list[AlignedPair] = []

    # 简单序列对齐：译文句序通常与源文一致
    max_len = max(len(source_sents), len(target_sents))
    si, ti = 0, 0

    while si < len(source_sents) and ti < len(target_sents):
        src = source_sents[si]
        tgt = target_sents[ti]

        if not src.strip() or len(src.strip()) < 5:
            si += 1
            continue
        if not tgt.strip() or len(tgt.strip()) < 5:
            ti += 1
            continue

        # 计算对齐置信度
        score = _alignment_score(src, tgt)

        pairs.append(AlignedPair(
            source_seg=src.strip(),
            target_seg=tgt.strip(),
            alignment_score=score,
        ))
        si += 1
        ti += 1

    return pairs


def _split_sentences(text: str) -> list[str]:
    """中英文混合句级分割"""
    # 先按MD结构分块
    blocks = re.split(r'(\n#{1,6}\s+.+\n)', text)

    sentences: list[str] = []
    for block in blocks:
        if block.startswith('\n#'):
            sentences.append(block.strip())
        else:
            # 按标点符号切句
            segs = re.split(r'(?<=[.;!?。；！？])\s+', block)
            for seg in segs:
                # 进一步按换行切
                sub_segs = [s.strip() for s in seg.split('\n') if s.strip()]
                sentences.extend(sub_segs)

    return [s for s in sentences if s and len(s) > 3]


def _alignment_score(src: str, tgt: str) -> float:
    """
    计算对齐置信度（0-1）。

    综合考虑：
      - 长度比（中文字数 vs 英文词数，合理比例 ~0.5-2.0）
      - 关键词重叠率（英文关键词中的字母是否出现在译文中）
    """
    # 长度比
    src_len = len(src)
    tgt_len = len(tgt)
    if src_len == 0 or tgt_len == 0:
        return 0.0

    ratio = min(src_len, tgt_len) / max(src_len, tgt_len)

    # 关键词重叠（简单的字母/中文匹配）
    src_words = set(re.findall(r'[a-zA-Z]+', src.lower()))
    tgt_text = tgt.lower()
    if src_words:
        hits = sum(1 for w in src_words if w.lower() in tgt_text)
        word_overlap = hits / len(src_words)
    else:
        word_overlap = 0.5  # 无法判断时给中性分

    return round(ratio * 0.4 + word_overlap * 0.6, 3)
