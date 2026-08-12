"""
译前Sub-Agent
=============
Vibe Coder A | v1.3 | 2026-08-10 (D5)

职责：译前agent说明书 + 工作流协调（按工作流调用两个技能）
内部：技能一·策略制定（LLM·先执行）→ 技能二·术语提取（LLM·后执行·多chunk分批全查→合并）

输入：PreprocessResult + UserPrefs
输出：PreTranslateResult（chunks + strategy_book + term_table）

D2更新：继承BaseAgent框架，支持统一spawn/超时/取消/重试
D3更新：策略/术语Prompt调优（few-shot示例·提取标准收紧·JSON约束强化）·方向感知
D5更新（技能化重构）：
  - 通用技能框架：core/skills/skill.py（Skill基类+登记处·说明书从skill.md运行时加载）
  - 技能目录化：core/skills/<skill_dir>/（skill.md说明书 + reference/参考材料 + scripts/实现）
  - 本模块只保留三件事：agent说明书（注明两个技能+工作流）、工作流协调、BaseAgent封装
  - 每次LLM调用 = agent说明书 + 技能说明书（追加式·由技能full_system_prompt组装）
  - 术语提取：单chunk读全文；多chunk分批全查（每chunk一次调用）→ 合并去重（首次译法优先）
  - 翻译方向记录进策略书 direction 字段（策略技能从输入记录），术语技能以该字段路由方向
  - RAG查证逻辑随技能迁入 term_extraction/scripts，仍按配置开关休眠（成员C完成后打开即接入）
  - 结构解析/文档分块仍由主agent预处理完成（本模块不重复处理）
"""

from transagent.interface import (
    PreprocessResult, PreTranslateResult, TermTable, UserPrefs, TermSource,
)
from transagent.backend.core.agent_framework import (
    BaseAgent, AgentContext, AgentResult, AgentRegistry, register_agent,
)
from transagent.backend.core.skills.pre_skills.strategy_formulation.scripts.strategy_skill import StrategySkill
from transagent.backend.core.skills.pre_skills.term_extraction.scripts.term_skill import TermExtractionSkill


# ══════════════════════════════════════════════════════════════════
# 译前agent 系统提示词（注明两个技能 + 工作流）
# ══════════════════════════════════════════════════════════════════

PRE_AGENT_SYSTEM_PROMPT = """你是"译前Sub-Agent"——负责翻译开始前的全部准备工作。
你收到的文档已经由上游主agent完成结构解析（不可译区域已用{NT_n}占位符保护、可译标签用{T_n}占位符）
和文档分块，你直接基于处理后的文本工作。

你具备以下两项技能，系统按工作流调用；每次调用只会在本说明书之后追加一份技能说明书，
你只需执行追加的那份技能对应的工作：

【技能一：策略制定】（strategy_formulation）
在翻译开始前分析文档：识别ICT子领域、评级难度、判断风格、确定直译/意译比例、明确目标读者。
输出：翻译策略书（JSON·含ICT子领域标签）。

【技能二：术语提取】（term_extraction）
从ICT文档中提取领域术语并确定目标语言译法，按置信度分流（正式术语/待确认）。
输出：项目术语表（JSON）。

工作流程（固定顺序·不可颠倒）：
1. 必须先执行技能一，拿到ICT子领域标签后，才能执行技能二。
2. 技能二依赖技能一的产出：术语消歧必须结合领域标签
   （如"container"在K8s→"容器"，在物流→"集装箱"）。
3. 长文档（多chunk）时，技能二会按chunk分批执行（每次一个部分），由系统合并各批结果并去重。

执行规则：
- 每次调用只执行追加的那份技能说明书对应的工作，不得执行另一项技能。
- 只输出该技能要求的JSON，不要输出其他内容，不要添加任何解释。
"""


# ══════════════════════════════════════════════════════════════════
# 工作流协调器（按工作流调用skill·代码固定顺序）
# ══════════════════════════════════════════════════════════════════

async def spawn_pre_translate(
    preprocess: PreprocessResult,
    user_prefs: UserPrefs,
    direction: str = "auto",
) -> PreTranslateResult:
    """
    译前Sub-Agent主入口（工作流协调器）。

    执行顺序（写死在代码里，AI不能跳步）：
      1. 技能一·策略制定（先执行·产出领域标签）
      2. 技能二·术语提取（后执行·携带领域标签消歧）
         单chunk → 一次读全文；多chunk → 每chunk一次调用（分批全查）→ 合并去重

    Args:
        direction: "en_to_zh" | "zh_to_en" | "auto"（默认·按文档语言自动检测）
    """
    full_text = preprocess.protected_md
    chunks = preprocess.chunks

    # ── 方向解析（auto → 语言检测）──
    if direction == "auto":
        direction = _detect_direction(full_text)
        print(f"[PreAgent] 方向自动检测: {direction}")

    # 技能实例化时注入agent说明书（技能不反向依赖agent模块·解耦）
    strategy_skill = StrategySkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)
    term_skill = TermExtractionSkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)

    # ── 步骤1：调用技能一（策略制定·翻译方向随策略书记录）──
    strategy_book = await strategy_skill.execute(
        md_text=full_text, user_prefs=user_prefs, direction=direction,
    )

    # ── 步骤2：调用技能二（术语提取·分批全查·方向以策略书direction字段为准）──
    batches: list[TermTable] = []
    if len(chunks) <= 1:
        print("[PreAgent] 单chunk → 技能二一次读全文")
        batches.append(await term_skill.execute(
            fragment=full_text, strategy=strategy_book,
            user_prefs=user_prefs, part_label="全文",
        ))
    else:
        n = len(chunks)
        print(f"[PreAgent] 多chunk({n}) → 技能二分批全查，每chunk一次调用")
        for i, chunk in enumerate(chunks):
            batches.append(await term_skill.execute(
                fragment=chunk.source_text, strategy=strategy_book,
                user_prefs=user_prefs,
                part_label=f"第{i + 1}/{n}部分",
            ))

    term_table = _merge_batches(batches)

    return PreTranslateResult(
        chunks=preprocess.chunks,
        strategy_book=strategy_book,
        term_table=term_table,
        placeholder_map=preprocess.placeholder_map,
    )


def _merge_batches(batches: list[TermTable]) -> TermTable:
    """
    合并各批术语表：去重（首次译法优先）+ 统计。

    跨批去重规则：以"先到先得"为准——同一术语在批次1出现后，
    批次2/3中的同术语（即使译法不同）直接丢弃。
    """
    merged = TermTable()
    seen: set[str] = set()
    for b in batches:
        for e in b.entries:
            if e.term and e.term not in seen:
                seen.add(e.term)
                merged.entries.append(e)
        for e in b.pending_entries:
            if e.term and e.term not in seen:
                seen.add(e.term)
                merged.pending_entries.append(e)

    merged.total_count = len(merged.entries) + len(merged.pending_entries)
    merged.rag_hit_count = sum(b.rag_hit_count for b in batches)
    merged.web_search_count = sum(b.web_search_count for b in batches)
    merged.llm_gen_count = sum(
        1 for e in (merged.entries + merged.pending_entries)
        if e.source == TermSource.LLM_GEN.value
    )
    return merged


def _detect_direction(md_text: str) -> str:
    """简单语言检测：CJK字符占比 >20% → zh_to_en，否则 en_to_zh。"""
    cjk = sum(1 for ch in md_text if "一" <= ch <= "鿿")
    total = len(md_text.strip())
    if total == 0:
        return "en_to_zh"
    return "zh_to_en" if cjk / total > 0.2 else "en_to_zh"


# ══════════════════════════════════════════════════════════════════
# BaseAgent 封装（D2新增·D5保持）
# ══════════════════════════════════════════════════════════════════

@register_agent
class PreTranslateAgent(BaseAgent):
    """
    译前Sub-Agent（BaseAgent封装）。

    使用方式：
        # 方式1：直接调用静态 spawn 函数（向后兼容）
        result = await spawn_pre_translate(preprocess, user_prefs)

        # 方式2：通过 BaseAgent.run()（统一框架）
        agent = PreTranslateAgent(context=AgentContext.simple("PreAgent", timeout=180))
        result = await agent.run(preprocess, user_prefs)
        if result.success:
            pre_result = result.data  # PreTranslateResult

        # 方式3：通过注册中心
        agent = AgentRegistry.create("PreTranslateAgent")
    """

    @property
    def agent_name(self) -> str:
        return "PreTranslateAgent"

    def default_context(self) -> AgentContext:
        return AgentContext(
            agent_name=self.agent_name,
            timeout_seconds=180.0,  # 策略+术语两次LLM调用，给3分钟
        )

    async def execute(
        self, preprocess: PreprocessResult, user_prefs: UserPrefs
    ) -> PreTranslateResult:
        """执行译前流程：策略制定 → 术语提取"""
        return await spawn_pre_translate(preprocess, user_prefs)
