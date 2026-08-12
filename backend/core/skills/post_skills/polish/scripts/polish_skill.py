"""
技能二：润色（译后）·实现脚本
=============================
Vibe Coder A | v1.0 | 2026-08-10 (D5)

说明书：本目录上级的 skill.md（由Skill基类运行时加载·双方向章节合一）。
本文件只放实现：组装用户消息 → 调用LLM → 返回（终稿, 润色说明）。

方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en）。

输入：源文 + 初译稿 + 质检报告 + 策略书
输出：（终稿, 润色说明）——修复质检问题 + 消除翻译腔 + 提升自然度

降级约定：润色失败时技能内部降级（交付初译稿）——"润色失败交付初译稿"是本技能职责内兜底。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat
不依赖：技能一、译后agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

from transagent.interface import (
    StrategyBook, QAResult,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class PolishSkill(Skill):
    """技能二：润色。输入 源文+初译稿+质检报告+策略书 → 输出（终稿, 润色说明）。"""
    name = "polish"
    description = "根据质检报告修复问题并润色，输出终稿"
    skill_dir = "post_skills/polish"
    temperature = 0.4
    max_tokens = 8000
    json_mode = False

    async def execute(
        self,
        source_md: str,
        draft: str,
        qa_result: QAResult,
        strategy_book: StrategyBook,
    ) -> tuple[str, str]:
        """执行一次润色（含校对：修复+润色一次完成）。

        Args:
            source_md: 源文（前2000字·仅作语义核对）
            draft: 初译稿
            qa_result: 质检报告（问题必须逐条修复）
            strategy_book: 策略书（读取其 direction 字段路由润色原则）

        Returns:
            (终稿, 润色说明)
        """
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        issues_text = "\n".join([
            f"- [{i.severity}] {i.location}: {i.type} - {i.description}"
            for i in qa_result.issues
        ]) if qa_result.issues else "（无具体问题）"

        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy_book.ict_domain}
目标风格：{strategy_book.style}

## 质检报告（总分{qa_result.total_score}）
- 术语准确性: {qa_result.term_accuracy}
- 语义忠实度: {qa_result.semantic_fidelity}
- 代码完整性: {qa_result.code_integrity}
- 流畅性: {qa_result.fluency}
- 风格匹配: {qa_result.style_match}

## 问题清单（必须逐条修复）
{issues_text}

## 初译稿
{draft}

## 源文参考（前2000字·仅作语义核对）
{source_md[:2000]}"""

        try:
            final_text = await chat(
                self.full_system_prompt(), user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            final = final_text if isinstance(final_text, str) else str(final_text)
            return final, _build_polish_notes(qa_result, direction)
        except Exception as e:
            print(f"[PolishSkill] 润色失败，返回初译稿: {e}")
            fallback = "润色失败·交付初译稿" if direction != "zh_to_en" else "Polish failed; delivering draft"
            return draft, f"{fallback}: {e}"


def _build_polish_notes(qa_result: QAResult, direction: str) -> str:
    """按质检结果生成润色说明（方向感知）。"""
    en = direction == "zh_to_en"
    if qa_result.total_score >= 9.5:
        return "Draft quality excellent, minimal touch-up" if en else "初译质量极高，仅做微调"
    if not qa_result.issues:
        return "Reduced awkward phrasing, improved fluency" if en else "消除翻译腔·提升自然度"
    return (
        f"Fixed {len(qa_result.issues)} issues, improved fluency"
        if en else
        f"修复{len(qa_result.issues)}个问题·消除翻译腔·提升自然度"
    )
