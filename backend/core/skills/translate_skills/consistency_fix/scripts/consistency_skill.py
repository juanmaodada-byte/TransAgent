"""
技能二：一致性检查与修复（译中）·实现脚本
=========================================
Vibe Coder A | v1.1 | 2026-08-10 (D5)

说明书：本目录上级的 skill.md（由Skill基类运行时加载）。
本文件只放实现：Python确定性预检 → 发现问题才触发LLM修复 → 返回统一译文。

方向路由：以策略书 direction 字段为准（用于区分源语言原文·构建修复Prompt）。

流程：Python确定性预检（毫秒级·零成本）→ 预检全部通过则直接合并（0次LLM调用）→
发现问题则LLM条件修复（1次LLM调用）。

降级约定：LLM修复失败时在技能内部降级（直接合并初译稿），不抛异常——
"修复失败交付初译稿"是本技能职责内的兜底。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + config
不依赖：技能一、译中agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import json
import re

from transagent.interface import (
    Chunk, TermTable, StrategyBook, ConsistencyReport,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class ConsistencySkill(Skill):
    """
    技能二：一致性检查与修复。输入 多chunk初译稿 + 术语表 + 策略书 → 输出统一译文。

    内部流程：
      1. Python确定性预检（占位符/代码块围栏/术语一致性·跨chunk汇总）——零成本
      2. 预检全部通过 → 直接合并（0次LLM调用）
      3. 预检发现问题 → LLM条件修复（1次LLM调用）→ 修复失败则降级为直接合并
    """
    name = "consistency_fix"
    description = "多chunk一致性：Python预检 + LLM条件修复，输出统一完整译文"
    skill_dir = "translate_skills/consistency_fix"
    temperature = 0.3
    max_tokens = 8000
    json_mode = False

    async def execute(
        self,
        draft_parts: list[str],
        chunks: list[Chunk],
        term_table: TermTable,
        strategy_book: StrategyBook,
    ) -> tuple[str, ConsistencyReport]:
        """执行一致性检查与修复。

        Args:
            draft_parts: 各chunk初译稿（与chunks同序）
            strategy_book: 策略书（本技能读取其 direction 字段区分源语言原文）

        Returns:
            (最终译文, 一致性报告)
        """
        cfg = get_config().pipeline
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由
        report = ConsistencyReport()
        precheck_issues = _consistency_precheck(draft_parts, term_table, chunks, direction)
        report.issues_found = len(precheck_issues)
        report.details = precheck_issues

        if not precheck_issues:
            # 预检通过 → 直接合并，零额外LLM调用（最常见路径）
            report.precheck_passed = True
            report.llm_fix_triggered = False
            return "\n\n".join(draft_parts), report

        # 预检发现问题 → 触发LLM修复
        report.precheck_passed = False
        report.llm_fix_triggered = True
        try:
            fix_prompt = _build_consistency_fix_prompt(
                draft_parts, chunks, term_table, strategy_book, precheck_issues, direction
            )
            fixed = await chat(
                self.full_system_prompt(), fix_prompt,
                temperature=cfg.consistency_temperature,
                max_tokens=cfg.consistency_max_tokens,
            )
            final_draft = fixed if isinstance(fixed, str) else str(fixed)
            if not final_draft.strip():
                # LLM返回空 → 降级为直接合并
                final_draft = "\n\n".join(draft_parts)
            return final_draft, report
        except Exception as e:
            print(f"[ConsistencySkill] 一致性修复失败，降级为直接合并: {e}")
            return "\n\n".join(draft_parts), report


def _consistency_precheck(
    drafts: list[str],
    term_table: TermTable,
    chunks: list[Chunk],
    direction: str = "en_to_zh",
    max_term_checks: int = 20,
) -> list[dict]:
    """
    Python确定性预检（毫秒级·零成本）。D4强化：

      1. 占位符完整性：{NT_n} 与 {T_n} 双保护
      2. 代码块围栏完整性：``` 数量一致
      3. 术语一致性（真实检测·方向感知）：
         - translate术语：源chunk含该术语 → 译文必须出现术语表指定译法
           若未出现：仍保留源语言原文 → 标记"未翻译"；否则 → 标记"译法缺失（可能译成其他说法）"
         - notranslate术语：源chunk含该术语 → 译文必须原样保留原文，否则标记"被改译"
         - 跨chunk不一致：同一术语在部分chunk用指定译法、部分chunk缺失/不同 → 汇总标记

    预检只做"确定性信号"检测，供 LLM 修复阶段参考，因此宁可多报也不漏报
    （条件触发的LLM修复由 skill.md 说明书把关质量）。

    Args:
        direction: 用于区分源语言原文——en_to_zh 检测英文原文残留，zh_to_en 检测中文原文残留
        max_term_checks: 最多检查的术语数（控制成本）
    """
    issues: list[dict] = []

    def _add(issue: dict) -> None:
        if len(issues) < 50:  # 控制LLM修复输入规模
            issues.append(issue)

    # 1. 占位符完整性（{NT_n} + {T_n} 双保护）
    placeholder_re = re.compile(r'\{N?T_\d+\}')
    for i, (draft, chunk) in enumerate(zip(drafts, chunks)):
        expected = set(placeholder_re.findall(chunk.source_text))
        actual = set(placeholder_re.findall(draft))
        missing = expected - actual
        if missing:
            _add({
                "type": "missing_placeholder",
                "chunk": i + 1,
                "missing": sorted(missing),
            })

    # 2. 代码块围栏完整性
    for i, draft in enumerate(drafts):
        src_blocks = len(re.findall(r'```', chunks[i].source_text))
        tgt_blocks = len(re.findall(r'```', draft))
        if src_blocks != tgt_blocks:
            _add({
                "type": "code_block_mismatch",
                "chunk": i + 1,
                "expected": src_blocks,
                "actual": tgt_blocks,
            })

    # 3. 术语一致性（跨chunk·方向感知）
    if not term_table.entries:
        return issues

    for entry in term_table.entries[:max_term_checks]:
        term = (entry.term or "").strip()
        required = (entry.translation or "").strip()
        if not term or not required:
            continue
        term_l = term.lower()
        src_chunks = [i for i, c in enumerate(chunks) if term_l in c.source_text.lower()]
        if not src_chunks:
            continue  # 术语未出现在源文中，跳过

        for i in src_chunks:
            draft_l = drafts[i].lower()
            if entry.action == "notranslate":
                if term_l not in draft_l:
                    _add({
                        "type": "term_notranslate_modified",
                        "chunk": i + 1,
                        "term": term,
                        "note": "notranslate术语必须原样保留原文",
                    })
                continue

            if required.lower() in draft_l:
                continue  # 指定译法已使用 ✓
            if term_l in draft_l:
                _add({
                    "type": "term_untranslated",
                    "chunk": i + 1,
                    "term": term,
                    "translation": required,
                    "note": "术语仍保留源语言原文，疑似未翻译",
                })
            else:
                _add({
                    "type": "term_translation_missing",
                    "chunk": i + 1,
                    "term": term,
                    "translation": required,
                    "note": "源文含该术语但译文未出现指定译法，可能译成了其他说法",
                })

    # 4. 跨chunk不一致汇总（同一术语部分chunk用指定译法、部分chunk缺失/不同）
    if len(drafts) > 1:
        for entry in term_table.entries[:max_term_checks]:
            term_l = (entry.term or "").lower()
            required_l = (entry.translation or "").lower()
            if not term_l or not required_l:
                continue
            src_idx = [i for i, c in enumerate(chunks) if term_l in c.source_text.lower()]
            if len(src_idx) < 2:
                continue
            ok = sum(1 for i in src_idx if required_l in drafts[i].lower())
            if 0 < ok < len(src_idx):
                _add({
                    "type": "term_cross_chunk_inconsistent",
                    "term": entry.term,
                    "translation": entry.translation,
                    "chunks": [i + 1 for i in src_idx],
                    "note": "术语在部分chunk使用指定译法、部分chunk缺失或不同，需统一",
                })

    return issues


def _build_consistency_fix_prompt(
    draft_parts: list[str],
    chunks: list[Chunk],
    term_table: TermTable,
    strategy_book: StrategyBook,
    precheck_issues: list[dict],
    direction: str,
) -> str:
    """构建一致性修复Prompt：携带方向+策略上下文+术语表+预检问题清单+各chunk初译稿。"""
    dir_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
    term_lines = "\n".join([
        f"- {e.term} → {e.translation}" + ("【不译】" if e.action == "notranslate" else "")
        for e in term_table.entries[:20]
    ]) or "（无术语表）"

    fix_prompt = f"""
翻译方向：{dir_label}
ICT子领域：{strategy_book.ict_domain} | 风格：{strategy_book.style} | 直译/意译比例：{strategy_book.literal_ratio}

## 项目术语表
{term_lines}

## 预检发现的问题（必须逐条修复）
{json.dumps(precheck_issues, ensure_ascii=False)}

## 各chunk初译稿
"""
    for i, (chunk, part) in enumerate(zip(chunks, draft_parts)):
        fix_prompt += f"\n### chunk_{i + 1}\n{part}\n"
    fix_prompt += "\n请合并为一份统一的完整译文，逐条修复上述全部问题。直接输出修复后的MD全文。"
    return fix_prompt
