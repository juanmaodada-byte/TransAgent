"""
技能一：质检（译后）·实现脚本
=============================
Vibe Coder A | v1.0 | 2026-08-10 (D5)

说明书：本目录上级的 skill.md（由Skill基类运行时加载·双方向章节合一）。
本文件只放实现：组装用户消息 → 调用LLM → 解析为QAResult。

方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en）。

输入：源文 + 初译稿 + 术语表 + 策略书
输出：QAResult（5维评分 + 问题列表 + 总结）

降级约定：质检失败时技能内部降级（基础评分8.0）——"质检降级用基础评分"是本技能职责内兜底。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat
不依赖：技能二、译后agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

from transagent.interface import (
    TermTable, StrategyBook, QAResult, QAIssue,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class QualityInspectionSkill(Skill):
    """技能一：质检。输入 源文+初译稿+术语表+策略书 → 输出质检报告。"""
    name = "quality_inspection"
    description = "ICT翻译质检：术语/语义/代码完整性/流畅性/风格 5维评分并定位问题"
    skill_dir = "post_skills/quality_inspection"
    temperature = 0.3
    max_tokens = 4000
    json_mode = True

    async def execute(
        self,
        source_md: str,
        draft: str,
        term_table: TermTable,
        strategy_book: StrategyBook,
    ) -> QAResult:
        """执行一次质检。

        Args:
            source_md: 源文（受保护MD·前3000字对照）
            draft: 初译稿（前4000字质检）
            strategy_book: 策略书（读取其 direction 字段路由质检标准）
        """
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        term_context = "\n".join([
            f"- {e.term} → {e.translation}" + ("【不译】" if e.action == "notranslate" else "")
            for e in term_table.entries[:15]
        ]) if term_table.entries else "（无术语表）"

        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy_book.ict_domain}
目标风格：{strategy_book.style}

## 项目术语表
{term_context}

## 源文（前3000字）
{source_md[:3000]}

## 译文（前4000字）
{draft[:4000]}"""

        try:
            result = await chat(
                self.full_system_prompt(), user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=self.json_mode,
            )
            if isinstance(result, dict):
                issues = [
                    QAIssue(
                        location=iss.get("location", ""),
                        severity=iss.get("severity", "minor"),
                        type=iss.get("type", ""),
                        description=iss.get("description", ""),
                    )
                    for iss in result.get("issues", [])
                ]
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
            print(f"[QualityInspectionSkill] 质检失败: {e}")

        return QAResult(total_score=8.0, summary="质检降级·使用基础评分")
