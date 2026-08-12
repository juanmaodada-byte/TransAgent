"""
译后Sub-Agent
=============
Vibe Coder A | v1.2 | 2026-08-10 (D5)

职责：译后agent说明书 + 工作流协调（按工作流调用两个技能）
内部：技能一·质检（LLM·先执行）→ 技能二·润色（LLM·后执行·含校对）

输入：source_md + draft + term_table + strategy_book
输出：PostTranslateResult（终稿 + 质检报告 + 润色说明）

D2更新：继承BaseAgent框架，支持统一spawn/超时/取消/重试
D5更新（技能化重构·目录化）：
  - 技能目录化：core/skills/quality_inspection/（技能一·质检）、core/skills/polish/（技能二·润色）
    —— 各含 skill.md（说明书·双方向章节合一）+ reference/ + scripts/
  - 本模块只保留三件事：agent说明书（注明两个技能+工作流）、工作流协调、BaseAgent封装
  - 每次LLM调用 = agent说明书 + 技能说明书（追加式·由技能full_system_prompt组装）
  - 翻译方向以策略书 direction 字段为准；direction参数降为兼容兜底（策略书未记录时用）
"""

from transagent.interface import (
    TermTable, StrategyBook, PostTranslateResult,
)
from transagent.backend.core.agent_framework import (
    BaseAgent, AgentContext, AgentResult, AgentRegistry, register_agent,
)
from transagent.backend.core.skills.post_skills.quality_inspection.scripts.quality_skill import QualityInspectionSkill
from transagent.backend.core.skills.post_skills.polish.scripts.polish_skill import PolishSkill


# ══════════════════════════════════════════════════════════════════
# 译后agent 系统提示词（注明两个技能 + 工作流）
# ══════════════════════════════════════════════════════════════════

POST_AGENT_SYSTEM_PROMPT = """你是"译后Sub-Agent"——负责翻译完成后的质量把关。
你收到的材料：源文、初译稿、项目术语表、翻译策略书。

你具备以下两项技能，系统按工作流调用；每次调用只会在本说明书之后追加一份技能说明书，
你只需执行追加的那份技能对应的工作：

【技能一：质检】（quality_inspection）
对比源文和译文，按ICT专项5维标准（术语/语义/代码完整性/流畅性/风格）评分并定位问题。
输出：质检报告（JSON·5维评分+问题列表）。

【技能二：润色】（polish）
根据质检报告修复问题，消除翻译腔，提升母语自然度，输出终稿。
输出：润色后的完整译文（MD文本）。

工作流程（固定顺序·由系统编排）：
1. 先执行技能一（质检），拿到质检报告。
2. 再执行技能二（润色），根据质检报告修复+润色（含校对·一次完成）。
3. 翻译方向以策略书中的"翻译方向"字段为准；{NT_n}/{T_n}占位符原样保留。

执行规则：
- 每次调用只执行追加的那份技能说明书对应的工作，不得执行另一项技能。
- 技能一输出质检报告JSON；技能二输出润色后的完整译文。不要添加解释。
"""


# ══════════════════════════════════════════════════════════════════
# 工作流协调器（按工作流调用skill·代码固定顺序）
# ══════════════════════════════════════════════════════════════════

async def spawn_post_translate(
    source_md: str,
    draft: str,
    term_table: TermTable,
    strategy_book: StrategyBook,
    direction: str = "",
) -> PostTranslateResult:
    """
    译后Sub-Agent主入口（工作流协调器）。

    执行顺序（写死在代码里，AI不能跳步）：
      1. 技能一·质检（先执行·产出质检报告）
      2. 技能二·润色（后执行·根据质检报告修复+润色）

    Args:
        direction: 兼容参数——翻译方向以策略书 direction 字段为准；
                   仅当策略书未记录（direction为空）时用作兜底，默认 "en_to_zh"
    """
    # 方向单一事实来源=策略书：未记录时用调用参数兜底
    if not strategy_book.direction:
        strategy_book.direction = direction or "en_to_zh"

    qa_skill = QualityInspectionSkill(agent_prompt=POST_AGENT_SYSTEM_PROMPT)
    polish_skill = PolishSkill(agent_prompt=POST_AGENT_SYSTEM_PROMPT)

    # ── Step 1: 调用技能一（质检）──
    qa_result = await qa_skill.execute(
        source_md=source_md, draft=draft,
        term_table=term_table, strategy_book=strategy_book,
    )

    # ── Step 2: 调用技能二（润色·含校对）──
    final_text, polish_notes = await polish_skill.execute(
        source_md=source_md, draft=draft,
        qa_result=qa_result, strategy_book=strategy_book,
    )

    return PostTranslateResult(
        final_text=final_text,
        qa_report=qa_result,
        polish_notes=polish_notes,
    )


# ══════════════════════════════════════════════════════════════════
# BaseAgent 封装（D2新增·D5保持）
# ══════════════════════════════════════════════════════════════════

@register_agent
class PostTranslateAgent(BaseAgent):
    """
    译后Sub-Agent（BaseAgent封装）。

    使用方式：
        # 方式1：直接调用静态 spawn 函数（向后兼容）
        result = await spawn_post_translate(source_md, draft, term_table, strategy_book)

        # 方式2：通过 BaseAgent.run()（统一框架）
        agent = PostTranslateAgent(context=AgentContext.simple("PostAgent", timeout=180))
        result = await agent.run(source_md, draft, term_table, strategy_book)
        if result.success:
            post_result = result.data  # PostTranslateResult

        # 方式3：通过注册中心
        agent = AgentRegistry.create("PostTranslateAgent")
    """

    @property
    def agent_name(self) -> str:
        return "PostTranslateAgent"

    def default_context(self) -> AgentContext:
        return AgentContext(
            agent_name=self.agent_name,
            timeout_seconds=180.0,  # 质检+润色两次LLM调用，给3分钟
        )

    async def execute(
        self,
        source_md: str,
        draft: str,
        term_table: TermTable,
        strategy_book: StrategyBook,
        direction: str = "",
    ) -> PostTranslateResult:
        """执行译后流程：质检 → 润色"""
        return await spawn_post_translate(
            source_md, draft, term_table, strategy_book, direction
        )
