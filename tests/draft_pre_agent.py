"""
译前agent·评审草稿
==================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

译前agent = agent说明书（注明两个技能+工作流）+ 工作流协调器（代码固定顺序调用技能）。
正式落地位置：transagent/backend/core/pre_agent.py

职责边界（解耦）：
  - 本模块不持有技能的详细说明书和实现（那是 draft_pre_skills 的）
  - 本模块只做三件事：
      ① agent身份说明书（PRE_AGENT_SYSTEM_PROMPT·注明两个技能+工作流）
      ② 按工作流调用技能（实例化时注入agent说明书·先技能一后技能二）
      ③ 合并术语批次结果（分批全查 → 去重·首次译法优先）
"""

from transagent.interface import (
    UserPrefs, StrategyBook, TermTable, TermSource, PreprocessResult,
)
from transagent.tests.draft_pre_skills import StrategySkill, TermExtractionSkill


# ══════════════════════════════════════════════════════════════════
# 一、译前agent 系统提示词（注明两个技能 + 工作流）
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
# 二、工作流协调器（按工作流调用skill·代码固定顺序）
# ══════════════════════════════════════════════════════════════════

async def run_pre_workflow(
    preprocess_result: PreprocessResult,
    user_prefs: UserPrefs,
    direction: str = "auto",
) -> tuple[StrategyBook, TermTable]:
    """
    译前工作流：按固定顺序调用两个skill。

    输入：主agent预处理产物（受保护MD + chunk列表 + 占位符表）——结构解析/分块在主agent完成
    输出：策略书 + 完整术语表

    固定顺序（写死在代码里，AI不能跳步）：
      1. StrategySkill（技能一·先执行·产出领域标签）
      2. TermExtractionSkill（技能二·后执行·携带领域标签消歧）
         单chunk → 一次读全文；多chunk → 每chunk一次调用（分批全查）→ 合并
    """
    full_text = preprocess_result.protected_md
    chunks = preprocess_result.chunks

    # 方向解析（auto → 语言检测）
    if direction == "auto":
        direction = _detect_direction(full_text)
        print(f"[PreWorkflow] 方向自动检测: {direction}")

    # 技能实例化时注入agent说明书（技能不反向依赖agent模块·解耦）
    strategy_skill = StrategySkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)
    term_skill = TermExtractionSkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)

    # ── 步骤1：调用技能一（策略制定）──
    strategy_book = await strategy_skill.execute(md_text=full_text, user_prefs=user_prefs)

    # ── 步骤2：调用技能二（术语提取·分批全查）──
    batches: list[TermTable] = []
    if len(chunks) <= 1:
        print("[PreWorkflow] 单chunk → 技能二一次读全文")
        batches.append(await term_skill.execute(
            fragment=full_text, strategy=strategy_book,
            direction=direction, user_prefs=user_prefs, part_label="全文",
        ))
    else:
        n = len(chunks)
        print(f"[PreWorkflow] 多chunk({n}) → 技能二分批全查，每chunk一次调用")
        for i, chunk in enumerate(chunks):
            batches.append(await term_skill.execute(
                fragment=chunk.source_text, strategy=strategy_book,
                direction=direction, user_prefs=user_prefs,
                part_label=f"第{i + 1}/{n}部分",
            ))

    term_table = _merge_batches(batches)
    return strategy_book, term_table


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
