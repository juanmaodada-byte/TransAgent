"""
译后Sub-Agent
=============
Vibe Coder A | v1.0 | 2026-08-06

职责：ICT专项质检 + 润色（含校对）
内部：2次LLM调用（串行·质检先行→润色后行）

输入：source_md + draft + term_table + strategy_book
输出：PostTranslateResult（终稿 + 质检报告 + 润色说明）
"""

import json
from transagent.interface import (
    TermTable, StrategyBook, PostTranslateResult, QAResult, QAIssue,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config

# ── Prompt模板 ────────────────────────────────────────────────────

QA_EN_ZH_PROMPT = """你是ICT翻译质量审核专家。你的任务是对比源文和译文，按ICT专项6维标准评分并定位问题。

## ICT专项质检维度

| 维度 | 权重 | 检查要点 |
|------|------|---------|
| 术语准确性 | 30% | 术语是否按术语表使用·缩写首次出现给全称·ICT术语无错译 |
| 语义忠实度 | 30% | 源文语义完整传达·无漏译·无增译·无曲解 |
| 代码/参数完整性 | 15% | {NT_n}占位符完整保留·代码块未被动·不可译区域未被误译 |
| 流畅性 | 15% | 中文自然·无翻译腔·句子长度适中·主动语态 |
| 风格匹配 | 10% | 符合ICT文档风格（专业·简洁·主动语态·避免"您"） |

## 评分标准
- 10分：专业译员水准，无任何问题
- 9-9.5：极高质量，仅1-2处微瑕
- 8-8.5：良好，术语准确但流畅性可提升
- 7-7.5：基本可读但需要润色
- <7：存在较多问题

## 输出格式（JSON）

{
  "total_score": 9.1,
  "term_accuracy": 9.5,
  "semantic_fidelity": 9.2,
  "code_integrity": 10.0,
  "fluency": 8.5,
  "style_match": 8.8,
  "issues": [
    {
      "location": "段落3·第2句",
      "severity": "minor",
      "type": "翻译腔",
      "description": ""被部署到集群中" → 建议改为 "部署到集群中""
    }
  ],
  "summary": "术语准确·代码完整·2处翻译腔可优化"
}
"""

QA_ZH_EN_PROMPT = """You are an ICT translation quality reviewer. Your task is to compare the source (Chinese) with the translation (English) and score it across 5 dimensions, flagging any issues.

## QA Dimensions

| Dimension | Weight | Checkpoints |
|-----------|--------|-------------|
| Term Accuracy | 30% | Glossary terms used correctly; ICT abbreviations preserved; no mistranslation of technical terms |
| Semantic Fidelity | 30% | Full meaning conveyed; no omissions, additions, or distortions |
| Code/Param Integrity | 15% | {NT_n} placeholders intact; code blocks unmodified; untranslatable regions preserved |
| Fluency | 15% | Natural English; no Chinglish; appropriate sentence length; active voice |
| Style Match | 10% | ICT documentation style (professional, concise, no fluff) |

## Scoring
- 10: Professional translator quality
- 9-9.5: Excellent, 1-2 minor issues
- 8-8.5: Good, term accuracy solid but fluency could improve
- 7-7.5: Readable but needs polish
- <7: Significant issues

## Output format (JSON)

{
  "total_score": 9.1,
  "term_accuracy": 9.5,
  "semantic_fidelity": 9.2,
  "code_integrity": 10.0,
  "fluency": 8.5,
  "style_match": 8.8,
  "issues": [
    {
      "location": "para 1, sentence 2",
      "severity": "minor",
      "type": "Chinglish",
      "description": "awkward phrasing, suggested rewrite"
    }
  ],
  "summary": "Accurate terms, code intact, 2 fluency issues to polish"
}
"""

POLISH_EN_ZH_PROMPT = """你是ICT领域资深中文技术编辑。你的任务是根据质检报告修复问题，并提升译文的中文自然度。

## 修复原则

1. **修复质检问题**：逐条处理质检报告中标记的问题
2. **消除翻译腔**：
   - 被动句 → 主动句（"被部署" → "部署"；"被调用" → "调用"）
   - "的"字滥用 → 精简（"集群的状态" → "集群状态"）
   - 英文长句 → 中文短句（逗号拆分）
   - "进行"、"一个"等冗余词 → 删除
3. **提升母语自然度**：读起来像中文母语者写的技术文档
4. **不改语义**：只优化表达，不改变原文意思
5. **不改占位符**：{NT_n} 原样保留

## 输出

直接输出润色后的完整译文。不要添加解释、不要添加前缀。
"""

POLISH_ZH_EN_PROMPT = """You are a senior English technical editor specializing in ICT content. Your task is to fix issues flagged in the QA report and polish the translation to native-level English.

## Polish Principles

1. **Fix QA issues**: Address every issue flagged in the QA report
2. **Eliminate Chinglish**:
   - Overly literal translations → natural English phrasing
   - Run-on sentences → split into clear, concise sentences
   - Redundant words ("for the purpose of", "in order to") → simplify
   - Passive constructions where active would be clearer
3. **Native-level fluency**: Should read like it was originally written in English by an ICT professional
4. **Preserve meaning**: Only improve expression; do not alter the original meaning
5. **Preserve placeholders**: {NT_n} must stay as-is

## Output

Output the polished translation directly. No explanations, no prefixes.
"""


async def spawn_post_translate(
    source_md: str,
    draft: str,
    term_table: TermTable,
    strategy_book: StrategyBook,
    direction: str = "en_to_zh",
) -> PostTranslateResult:
    """
    译后Sub-Agent主入口。

    执行顺序：质检（LLM）→ 润色（LLM·根据质检报告修复+润色）

    Args:
        direction: "en_to_zh" (默认) 或 "zh_to_en"
    """
    cfg = get_config().pipeline

    # ── Step 1: 质检（LLM）──
    qa_result = await _inspect_quality(source_md, draft, term_table, strategy_book, cfg, direction)

    # ── Step 2: 润色（LLM·含校对）──
    final_text, polish_notes = await _polish(
        source_md, draft, qa_result, term_table, strategy_book, cfg, direction
    )

    return PostTranslateResult(
        final_text=final_text,
        qa_report=qa_result,
        polish_notes=polish_notes,
    )


async def _inspect_quality(
    source_md: str, draft: str, term_table: TermTable,
    strategy_book: StrategyBook, cfg, direction: str = "en_to_zh",
) -> QAResult:
    """质检 LLM调用"""

    if direction == "zh_to_en":
        qa_prompt_template = QA_ZH_EN_PROMPT
        term_label = "## Project Glossary"
        source_label = "## Source (first 3000 chars)"
        target_label = "## Translation"
        no_glossary = "(no glossary)"
        domain_label = "ICT Domain"
        style_label = "Target Style"
    else:
        qa_prompt_template = QA_EN_ZH_PROMPT
        term_label = "## 项目术语表"
        source_label = "## 源文（前3000字）"
        target_label = "## 译文"
        no_glossary = "（无术语表）"
        domain_label = "ICT子领域"
        style_label = "目标风格"

    term_context = "\n".join([
        f"- {e.term} → {e.translation} {'【不译】' if e.action == 'notranslate' else ''}"
        for e in term_table.entries[:15]
    ]) if term_table.entries else no_glossary

    qa_prompt = f"""
{domain_label}：{strategy_book.ict_domain}
{style_label}：{strategy_book.style}

{term_label}
{term_context}

{source_label}
{source_md[:3000]}

{target_label}
{draft[:4000]}
"""
    try:
        result = await chat(
            qa_prompt_template, qa_prompt,
            temperature=cfg.qa_temperature,
            max_tokens=cfg.qa_max_tokens,
            json_mode=True,
        )

        if isinstance(result, dict):
            issues = []
            for iss in result.get("issues", []):
                issues.append(QAIssue(
                    location=iss.get("location", ""),
                    severity=iss.get("severity", "minor"),
                    type=iss.get("type", ""),
                    description=iss.get("description", ""),
                ))

            return QAResult(
                total_score=float(result.get("total_score", 8.0)),
                term_accuracy=float(result.get("term_accuracy", 8.0)),
                semantic_fidelity=float(result.get("semantic_fidelity", 8.0)),
                code_integrity=float(result.get("code_integrity", 8.0)),
                fluency=float(result.get("fluency", 8.0)),
                style_match=float(result.get("style_match", 8.0)),
                issues=issues,
                summary=result.get("summary", ""),
            )
    except Exception as e:
        print(f"[PostAgent] 质检失败: {e}")

    # 降级：默认质检
    return QAResult(total_score=8.0, summary="质检降级·使用基础评分")


async def _polish(
    source_md: str, draft: str, qa_result: QAResult,
    term_table: TermTable, strategy_book: StrategyBook, cfg, direction: str = "en_to_zh",
) -> tuple[str, str]:
    """润色 LLM调用（含校对）"""

    if direction == "zh_to_en":
        polish_prompt_template = POLISH_ZH_EN_PROMPT
        domain_label = "ICT Domain"
        style_label = "Target Style"
        report_label = "QA Report"
        draft_label = "## Draft Translation"
        source_label = "## Source Reference (first 2000 chars, for semantic verification only)"
        fallback_msg = "Polish failed; delivering draft"
    else:
        polish_prompt_template = POLISH_EN_ZH_PROMPT
        domain_label = "ICT子领域"
        style_label = "目标风格"
        report_label = "## 质检报告（总分{qa_result.total_score}）"
        draft_label = "## 初译稿"
        source_label = "## 源文参考（前2000字·仅作语义核对）"
        fallback_msg = "润色失败·交付初译稿"

    issues_text = "\n".join([
        f"- [{i.severity}] {i.location}: {i.type} - {i.description}"
        for i in qa_result.issues
    ]) if qa_result.issues else "（无具体问题）" if direction == "en_to_zh" else "(no specific issues)"

    qa_report_text = f"QA Report (total score {qa_result.total_score})" if direction == "zh_to_en" else f"质检报告（总分{qa_result.total_score}）"

    polish_prompt = f"""
{domain_label}：{strategy_book.ict_domain}
{style_label}：{strategy_book.style}

## {qa_report_text}
- Term Accuracy: {qa_result.term_accuracy}
- Semantic Fidelity: {qa_result.semantic_fidelity}
- Code Integrity: {qa_result.code_integrity}
- Fluency: {qa_result.fluency}
- Style Match: {qa_result.style_match}

## Issues to Fix
{issues_text}

{draft_label}
{draft}

{source_label}
{source_md[:2000]}
"""
    try:
        final_text = await chat(
            polish_prompt_template, polish_prompt,
            temperature=cfg.polish_temperature,
            max_tokens=cfg.polish_max_tokens,
        )
        final = final_text if isinstance(final_text, str) else str(final_text)

        # 生成润色说明
        issue_count = len(qa_result.issues) if qa_result.issues else 0
        if qa_result.total_score >= 9.5:
            polish_notes = "初译质量极高，仅做微调" if direction == "en_to_zh" else "Draft quality excellent, minimal touch-up"
        elif issue_count == 0:
            polish_notes = "消除翻译腔·提升自然度" if direction == "en_to_zh" else "Reduced awkward phrasing, improved fluency"
        else:
            polish_notes = f"修复{issue_count}个问题·消除翻译腔·提升自然度" if direction == "en_to_zh" else f"Fixed {issue_count} issues, improved fluency"

        return final, polish_notes

    except Exception as e:
        print(f"[PostAgent] 润色失败，返回初译稿: {e}")
        return draft, f"{fallback_msg}: {e}"
