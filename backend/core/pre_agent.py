"""
译前Sub-Agent
=============
Vibe Coder A | v1.4 | 2026-08-15 (D6)

职责：译前agent说明书 + 工作流协调（按工作流调用两个技能）
内部：技能一·策略制定（LLM·先执行）→ 技能二·术语提取（LLM·后执行·多chunk分批全查→合并）

输入：SharedPool（source_md + chunks + user_prefs）
输出：写回 SharedPool（strategy_book + term_table）并返回 PreTranslateResult

D2更新：继承BaseAgent框架，支持统一spawn/超时/取消/重试
D3更新：策略/术语Prompt调优（few-shot示例·提取标准收紧·JSON约束强化）·方向感知
D5更新（技能化重构）：agent说明书 + 技能说明书（追加式）；术语分批全查合并去重；
       翻译方向记录进策略书 direction 字段；RAG查证随技能迁入仍按配置开关休眠
D6更新（共享池）：
  - 主入口改为池子派发器：spawn_pre_translate(pool) 走共享池；传旧参数自动构建池子（向后兼容）
  - 每个技能 execute 前 validate_pool 校验 requires（缺数据即拦），执行后写回池子 + 登记provides
  - 本模块只保留三件事：agent说明书、工作流协调、BaseAgent封装
"""

from transagent.interface import (
    PreprocessResult, PreTranslateResult, TermTable, UserPrefs,
)
from transagent.backend.core.agent_framework import (
    BaseAgent, AgentContext, AgentResult, AgentRegistry, register_agent,
)
from transagent.backend.core.shared_pool import SharedPool
from transagent.backend.core.skills.pre_skills.strategy_formulation.scripts.strategy_skill import StrategySkill
from transagent.backend.core.skills.pre_skills.term_extraction.scripts.term_skill import TermExtractionSkill
from transagent.backend.core.skills.pre_skills.term_translation.scripts.term_translation_skill import TermTranslationSkill


# ══════════════════════════════════════════════════════════════════
# 译前agent 系统提示词（注明两个技能 + 工作流）
# ══════════════════════════════════════════════════════════════════

PRE_AGENT_SYSTEM_PROMPT = """你是"译前Sub-Agent"——负责翻译开始前的全部准备工作。
你收到的文档已经由上游主agent完成结构解析（不可译区域已用{NT_n}占位符保护、可译标签用{T_n}占位符）
和文档分块，你直接基于处理后的文本工作。

你具备以下三项技能，系统按工作流调用；每次调用只会在本说明书之后追加一份技能说明书，
你只需执行追加的那份技能对应的工作：

【技能一：策略制定】（strategy_formulation）
在翻译开始前分析文档：识别ICT子领域、评级难度、判断风格、确定直译/意译比例、明确目标读者。
输出：翻译策略书（JSON·含ICT子领域标签）。

【技能二：术语提取】（term_extraction）
从ICT文档中提取领域术语（只提术语名 + 它在文中的上下文片段，不定译法）。
输出：术语候选列表（JSON）。

【技能三：术语翻译】（term_translation）
对已提取的术语确定目标语言译法：系统先做RAG术语库匹配（命中即复用库中译法），
你只翻译RAG未命中的术语。
输出：项目术语表（JSON）。

工作流程（固定顺序·不可颠倒）：
1. 必须先执行技能一，拿到ICT子领域标签后，才能执行技能二。
2. 技能二依赖技能一的产出：领域标签用于判断提取哪些术语。
3. 长文档（多chunk）时，技能二会按chunk分批执行（每次一个部分），由系统合并各批候选并去重。
4. 技能三在候选合并后执行一次：RAG优先匹配，未命中的术语才由你翻译（附上下文消歧）。

执行规则：
- 每次调用只执行追加的那份技能说明书对应的工作，不得执行另一项技能。
- 只输出该技能要求的JSON，不要输出其他内容，不要添加任何解释。
"""


# ══════════════════════════════════════════════════════════════════
# 工作流协调器（按工作流调用skill·代码固定顺序）
# ══════════════════════════════════════════════════════════════════

async def spawn_pre_translate(
    preprocess_or_pool,
    user_prefs: UserPrefs | None = None,
    direction: str = "auto",
) -> PreTranslateResult:
    """
    译前Sub-Agent主入口（D6池子派发器·兼容旧签名）。

    执行顺序（写死在代码里，AI不能跳步）：
      1. 技能一·策略制定（先执行·产出领域标签）
      2. 技能二·术语提取（后执行·携带领域标签消歧）
         单chunk → 一次读全文；多chunk → 每chunk一次调用（分批全查）→ 合并去重

    调用方式（二选一）：
      - 池子路径：spawn_pre_translate(pool)（主流程·共享池已填 source_md/chunks/user_prefs）
      - 兼容路径：spawn_pre_translate(preprocess_result, user_prefs, direction=...)（测试/演示脚本）
    """
    if isinstance(preprocess_or_pool, SharedPool):
        pool = preprocess_or_pool
    else:
        # 兼容路径：由旧参数构建池子
        preprocess = preprocess_or_pool
        pool = SharedPool()
        pool.preprocess_result = preprocess
        pool.source_md = preprocess.protected_md
        pool.chunks = preprocess.chunks
        pool.placeholder_map = preprocess.placeholder_map
        pool.user_prefs = user_prefs
    return await _pre_translate_from_pool(pool, direction)


async def _pre_translate_from_pool(
    pool: SharedPool,
    direction: str = "auto",
) -> PreTranslateResult:
    """核心工作流：从池子读上游 → 调技能 → 写回池子。"""
    full_text = pool.source_md
    chunks = pool.chunks
    if not pool.user_prefs:
        pool.user_prefs = UserPrefs()
    user_prefs = pool.user_prefs

    # ── 方向解析（auto → 语言检测）──
    if direction == "auto":
        direction = _detect_direction(full_text)
        print(f"[PreAgent] 方向自动检测: {direction}")

    # 技能实例化时注入agent说明书（技能不反向依赖agent模块·解耦）
    strategy_skill = StrategySkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)
    extract_skill = TermExtractionSkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)
    translate_skill = TermTranslationSkill(agent_prompt=PRE_AGENT_SYSTEM_PROMPT)

    # ── 步骤1：技能一（策略制定·翻译方向随策略书记录）──
    strategy_skill.validate_pool(pool)   # requires: source_md + user_prefs
    strategy_book = await strategy_skill.execute(
        md_text=full_text, user_prefs=user_prefs, direction=direction,
    )
    pool.strategy_book = strategy_book
    strategy_skill.mark_pool_provided(pool)

    # ── 步骤2：技能二（术语提取·只提术语名·分批全查）──
    extract_skill.validate_pool(pool)   # requires: source_md + strategy_book + user_prefs
    candidates: list[str] = []
    if len(chunks) <= 1:
        print("[PreAgent] 单chunk → 技能二一次读全文")
        candidates = await extract_skill.execute(
            fragment=full_text, strategy=strategy_book,
            user_prefs=user_prefs, part_label="全文",
        )
    else:
        n = len(chunks)
        print(f"[PreAgent] 多chunk({n}) → 技能二分批全查，每chunk一次调用")
        for i, chunk in enumerate(chunks):
            candidates.extend(await extract_skill.execute(
                fragment=chunk.source_text, strategy=strategy_book,
                user_prefs=user_prefs,
                part_label=f"第{i + 1}/{n}部分",
            ))
    pool.term_candidates = _dedup_candidates(candidates)
    extract_skill.mark_pool_provided(pool)

    # ── 步骤3：技能三（术语翻译·RAG优先·LLM兜底·一次）──
    if pool.term_candidates:
        translate_skill.validate_pool(pool)   # requires: term_candidates + strategy_book + user_prefs
        term_table = await translate_skill.execute(
            terms=pool.term_candidates,
            strategy=strategy_book,
            user_prefs=user_prefs,
        )
    else:
        print("[PreAgent] 无术语候选 → 空术语表")
        term_table = TermTable()
    # 双语术语表（术语原文 + 译文 + 来源）写回共享池，供译中/译后/交付消费
    pool.term_table = term_table
    translate_skill.mark_pool_provided(pool)

    return PreTranslateResult(
        chunks=chunks,
        strategy_book=strategy_book,
        term_table=term_table,
        placeholder_map=pool.placeholder_map,
    )


def _dedup_candidates(candidates: list[str]) -> list[str]:
    """
    合并各包/各chunk的术语候选：按术语名去重（保留首现）。

    跨包去重规则：以"先到先得"为准——同一术语在早出现的包中出现后，
    后续包的同术语直接丢弃。
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for c in candidates:
        term = str(c).strip()
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped


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
            timeout_seconds=600.0,  # D7: 策略+提取分包+翻译多次LLM调用，放宽防超时重跑
        )

    async def execute(
        self, preprocess: PreprocessResult, user_prefs: UserPrefs
    ) -> PreTranslateResult:
        """执行译前流程：策略制定 → 术语提取"""
        return await spawn_pre_translate(preprocess, user_prefs)
