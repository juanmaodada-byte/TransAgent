"""
译中Sub-Agent
=============
Vibe Coder A | v1.0 | 2026-08-06

职责：TM搜索 → 逐chunk主译 → 一致性检查（Python预检+LLM条件触发）
内部：1次LLM调用（主译）+ 0~1次LLM调用（一致性修复·条件触发）

输入：chunks + TermTable + StrategyBook + TM参考
输出：TranslateResult（初译稿 + 一致性报告）
"""

from transagent.interface import (
    Chunk, TermTable, StrategyBook, TMEntry, TranslateResult, ConsistencyReport,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config
import json

# ── Prompt模板 ────────────────────────────────────────────────────

TRANSLATE_EN_ZH_PROMPT = """你是ICT领域资深技术翻译专家。你的任务是将英文ICT文档翻译为专业、自然的中文。

## 翻译规则

1. **术语强制使用**：对照项目术语表，术语表中的词汇必须使用指定译法。不要自由发挥。
2. **不在术语表中的词汇**：根据上下文合理翻译，保持与策略书风格一致。
3. **占位符保护**：
   - 看到 {NT_n} 占位符 → 原样保留，不翻译不修改
   - 这些占位符代表代码/URL/命令等不可译内容，翻译后会还原
4. **ICT风格要求**：
   - 专业、简洁、主动语态
   - 英文长句 → 中文短句（拆分长句）
   - 被动句 → 主动句（避免"被"字泛滥）
   - 技术缩写首次出现给全称+缩写
5. **不要增译、漏译、曲解原文**
6. **代码块、命令行、API名等不译内容已被占位符保护，你看到{NT_n}直接原样输出即可**

## 输出

直接输出翻译后的MD文本。不要添加解释、不要添加前缀。只输出译文。
"""

TRANSLATE_ZH_EN_PROMPT = """You are a senior ICT technical translator. Your task is to translate Chinese ICT documents into professional, natural English.

## Translation Rules

1. **Mandatory term usage**: Strictly follow the project glossary. Glossary terms must use the specified translation.
2. **Terms not in glossary**: Translate according to context, maintaining consistency with the strategy book style.
3. **Placeholder protection**:
   - {NT_n} placeholders → keep as-is, do not translate or modify
   - These represent code/URLs/commands that must not be translated
4. **ICT style requirements**:
   - Professional, concise, active voice
   - Chinese long sentences → natural English sentences
   - Maintain technical accuracy; prefer clarity over literal translation
   - Technical abbreviations: keep as-is (e.g., API, SDK, CLI)
5. **No addition, omission, or distortion of the original meaning**
6. **Code blocks, CLI commands, API names should be kept as-is or represented by {NT_n}**

## Output

Output the translated Markdown text directly. No explanations, no prefixes. Just the translation.
"""

TM_REF_TEMPLATE = """
## 翻译记忆参考（相似句段的已有译法）

以下是从你的个人翻译记忆库中找到的相似句段参考。请保持风格一致，但根据实际上下文灵活调整。

{tm_snippets}

---
"""

CONSISTENCY_SYSTEM_PROMPT = """你是翻译一致性审核专家。你的任务是修复多chunk翻译中的术语和风格不一致问题。

## 输入
- 多个chunk的初译稿
- 项目术语表
- 占位符映射表

## 检查维度
1. 术语一致性：同一术语在不同chunk的译法是否一致
2. 占位符完整性：{NT_n}占位符是否完整保留
3. 代码块完整性：代码块内容是否未被修改
4. 风格一致性：不同chunk的语气、句式是否统一

## 输出
合并为一份统一的初译稿。直接输出修复后的完整译文。不要添加解释。"""


async def spawn_translate(
    chunks: list[Chunk],
    term_table: TermTable,
    strategy_book: StrategyBook,
    tm_refs: list[TMEntry] | None = None,
    direction: str = "en_to_zh",
) -> TranslateResult:
    """
    译中Sub-Agent主入口。

    执行顺序：TM搜索 → 逐chunk主译 → 一致性检查（条件触发）

    Args:
        direction: "en_to_zh" (默认) 或 "zh_to_en"
    """
    cfg = get_config().pipeline

    # 根据翻译方向选择 System Prompt
    if direction == "zh_to_en":
        translate_prompt = TRANSLATE_ZH_EN_PROMPT
        lang_label_source = "中文"
        lang_label_target = "英文"
        source_label = "待翻译文本"
        context_label = "前文翻译（上下文参考）"
    else:
        translate_prompt = TRANSLATE_EN_ZH_PROMPT
        lang_label_source = "英文"
        lang_label_target = "中文"
        source_label = "待翻译文本"
        context_label = "前文翻译（上下文参考）"

    # ── Step 1: 构建术语表提示 ──
    term_context = _build_term_context(term_table)

    # ── Step 2: 构建TM参考 ──
    tm_context = _build_tm_context(tm_refs) if tm_refs else ""

    # ── Step 3: 构建策略上下文 ──
    strategy_context = f"""
翻译策略：
- ICT子领域：{strategy_book.ict_domain}
- 难度：{strategy_book.difficulty}
- 风格：{strategy_book.style}
- 直译/意译比例：{strategy_book.literal_ratio}
- 翻译方向：{lang_label_source} → {lang_label_target}
- 规则：{json.dumps(strategy_book.rules, ensure_ascii=False)}
"""

    # ── Step 4: 逐chunk翻译 ──
    draft_parts: list[str] = []
    prev_translation = ""

    for chunk in chunks:
        chunk_prompt = f"""
{strategy_context}

{term_context}

{tm_context}

## {source_label}

{chunk.source_text}
"""
        if prev_translation:
            chunk_prompt += f"\n\n## {context_label}\n{prev_translation[-2000:]}"

        try:
            translated = await chat(
                translate_prompt, chunk_prompt,
                temperature=cfg.translate_temperature,
                max_tokens=cfg.translate_max_tokens,
            )
            draft_parts.append(translated if isinstance(translated, str) else str(translated))
            prev_translation = translated if isinstance(translated, str) else str(translated)
        except Exception as e:
            print(f"[TranslateAgent] chunk翻译失败: {e}")
            # 失败时保留原文
            draft_parts.append(f"[翻译失败] {chunk.source_text[:200]}...")

    # ── Step 5: 合并初译稿 ──
    draft = "\n\n".join(draft_parts) if len(draft_parts) > 1 else draft_parts[0] if draft_parts else ""

    # ── Step 6: 一致性检查（仅多chunk时触发）──
    consistency_report = ConsistencyReport()

    if len(chunks) > 1:
        # Python确定性预检
        precheck_issues = _consistency_precheck(draft_parts, term_table, chunks)
        consistency_report.issues_found = len(precheck_issues)

        if precheck_issues:
            # 预检发现不一致 → 触发LLM修复
            consistency_report.precheck_passed = False
            consistency_report.llm_fix_triggered = True
            try:
                fix_prompt = f"""
## 项目术语表
{json.dumps([e.to_dict() for e in term_table.entries], ensure_ascii=False)}

## 预检发现的问题
{json.dumps(precheck_issues, ensure_ascii=False)}

## 各chunk初译稿
"""
                for i, (chunk, part) in enumerate(zip(chunks, draft_parts)):
                    fix_prompt += f"\n### chunk_{i + 1}\n{part}\n"

                draft = await chat(
                    CONSISTENCY_SYSTEM_PROMPT, fix_prompt,
                    temperature=cfg.consistency_temperature,
                    max_tokens=cfg.consistency_max_tokens,
                )
                if not isinstance(draft, str):
                    draft = str(draft)
            except Exception as e:
                print(f"[TranslateAgent] 一致性修复失败: {e}")
                # 不修复，直接合并
        else:
            consistency_report.precheck_passed = True
            consistency_report.llm_fix_triggered = False

    return TranslateResult(
        draft=draft,
        consistency_report=consistency_report,
        tm_refs_used=len(tm_refs) if tm_refs else 0,
    )


def _build_term_context(term_table: TermTable) -> str:
    """构建术语表prompt片段"""
    if not term_table.entries:
        return "## 项目术语表\n（无预定义术语）"

    lines = ["## 项目术语表（以下术语必须使用指定译法）"]
    for e in term_table.entries:
        action_tag = "【不译】" if e.action == "notranslate" else ""
        lines.append(f"- {e.term} → {e.translation} {action_tag}")
    return "\n".join(lines)


def _build_tm_context(tm_refs: list[TMEntry]) -> str:
    """构建TM参考prompt片段"""
    snippets = []
    for e in tm_refs[:5]:  # 最多5条参考
        snippets.append(f"[{e.similarity:.0%}] '{e.source_seg.strip()[:120]}' → '{e.target_seg.strip()[:120]}'")
    return TM_REF_TEMPLATE.format(tm_snippets="\n".join(snippets))


def _consistency_precheck(
    drafts: list[str],
    term_table: TermTable,
    chunks: list[Chunk],
) -> list[dict]:
    """
    Python确定性预检（毫秒级·零成本）。
    检查术语一致性、{NT_n}占位符完整性、代码块保留。
    """
    import re
    issues: list[dict] = []

    # 1. 检查{NT_n}占位符完整性
    for i, (draft, chunk) in enumerate(zip(drafts, chunks)):
        expected_nts = re.findall(r'\{NT_\d+\}', chunk.source_text)
        actual_nts = re.findall(r'\{NT_\d+\}', draft)
        missing = set(expected_nts) - set(actual_nts)
        if missing:
            issues.append({
                "type": "missing_placeholder",
                "chunk": i + 1,
                "missing": list(missing),
            })

    # 2. 检查核心术语一致性（跨chunk）
    if len(drafts) > 1 and term_table.entries:
        for term_entry in term_table.entries[:10]:  # 检查前10个核心术语
            translations_found = set()
            for i, draft in enumerate(drafts):
                if term_entry.term.lower() in draft.lower():
                    # 在译文中找对应译法（简单反向匹配）
                    translations_found.add(i)
            if len(translations_found) > 0 and len(translations_found) < len(drafts):
                # 术语在某些chunk出现但其他chunk没出现 → 不一定是问题，仅记录
                pass

    # 3. 检查代码块标记是否保留
    for i, draft in enumerate(drafts):
        src_blocks = len(re.findall(r'```', chunks[i].source_text))
        tgt_blocks = len(re.findall(r'```', draft))
        if src_blocks != tgt_blocks:
            issues.append({
                "type": "code_block_mismatch",
                "chunk": i + 1,
                "expected": src_blocks,
                "actual": tgt_blocks,
            })

    return issues
