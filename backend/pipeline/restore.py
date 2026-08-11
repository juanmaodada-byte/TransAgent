"""
占位符还原
==========
Vibe Coder B | v1.0 | 2026-08-06

职责：将译文中的占位符还原。
      {NT_n} → 原文（不可译区域·原样保留）
      {T_n}  → 译文（Mermaid标签/alt文本·还原翻译后的文字）

确定性字符串替换，毫秒级，不经过LLM。

使用：
    from transagent.backend.pipeline.restore import restore_placeholders
    final = restore_placeholders(translated_md, pmap)
"""

import re
from transagent.interface import PlaceholderMap


def restore_placeholders(md_text: str, pmap: PlaceholderMap) -> str:
    """
    还原所有占位符。

    {NT_n} → pmap.nt_map["{NT_n}"] = 原文（不可译·原样保留）
    {T_n}  → pmap.t_map["{T_n}"]  = 译文（LLM翻译后的target文字）

    Args:
        md_text: 翻译后的MD文本（含占位符）
        pmap: 占位符映射表

    Returns:
        还原后的最终MD文本
    """
    result = md_text

    # Step 1: {NT_n} → 原文（不可译区域）
    for placeholder, original in pmap.nt_map.items():
        result = result.replace(placeholder, original)

    # Step 2: {T_n} → 译文（LLM翻译后的文字映射表）
    for placeholder, translated in pmap.t_map.items():
        result = result.replace(placeholder, translated)

    return result
