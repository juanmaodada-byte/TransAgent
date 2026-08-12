"""
译中Sub-Agent
=============
Vibe Coder A | v1.3 | 2026-08-10 (D5)

职责：译中agent说明书 + 工作流协调（按工作流调用两个技能）
内部：技能一·主译（逐chunk·串行/并行）→ 技能二·一致性检查与修复（多chunk条件触发）

输入：chunks + TermTable + StrategyBook + TM参考
输出：TranslateResult（初译稿 + 一致性报告）

D2更新：继承BaseAgent框架，支持统一spawn/超时/取消；新增并行chunk翻译能力
D4更新：主译Prompt调优·一致性预检强化·一致性检查重构为共用helper
D5更新（技能化重构）：
  - 技能目录化：core/skills/<skill_dir>/（skill.md说明书 + reference/参考材料 + scripts/实现）
  - 本模块只保留三件事：agent说明书（注明两个技能+工作流）、工作流协调、BaseAgent封装
  - 每次LLM调用 = agent说明书 + 技能说明书（追加式·由技能full_system_prompt组装）
  - 翻译方向以策略书 direction 字段为准（策略技能已从输入记录）——
    本模块仅在策略书未记录时用调用参数兜底（direction="" 时默认 en_to_zh）
  - chunk翻译失败由工作流降级（标记[翻译失败]保留原文·不影响其余chunk）
"""

from transagent.interface import (
    Chunk, TermTable, StrategyBook, TMEntry, TranslateResult, ConsistencyReport,
)
from transagent.backend.core.agent_framework import (
    BaseAgent, AgentContext, AgentResult, AgentRegistry, register_agent,
    spawn_parallel, SpawnTask,
)
from transagent.backend.core.skills.translate_skills.chunk_translate.scripts.chunk_translate_skill import ChunkTranslateSkill
from transagent.backend.core.skills.translate_skills.consistency_fix.scripts.consistency_skill import ConsistencySkill


# ══════════════════════════════════════════════════════════════════
# 译中agent 系统提示词（注明两个技能 + 工作流）
# ══════════════════════════════════════════════════════════════════

TRANSLATE_AGENT_SYSTEM_PROMPT = """你是"译中Sub-Agent"——负责文档的主体翻译。
你收到的材料：chunk列表（待翻译文本）、项目术语表、翻译策略书、TM参考（翻译记忆中的相似句段）。

你具备以下两项技能，系统按工作流调用；每次调用只会在本说明书之后追加一份技能说明书，
你只需执行追加的那份技能对应的工作：

【技能一：主译】（chunk_translate）
将单个chunk的文本译为目标语言：术语按项目术语表强制使用（标【不译】的保留原文）、
TM参考保持一致风格、遵循策略书。翻译方向以策略书中的"翻译方向"字段为准。
输出：该chunk的译文（MD文本·直接输出译文，无解释）。

【技能二：一致性检查与修复】（consistency_fix）
对比多chunk初译稿：修复术语/占位符/代码块/风格不一致，输出统一的完整译文。
翻译方向同样以策略书中的"翻译方向"字段为准。
输出：修复后的完整MD译文。

工作流程（固定顺序·由系统编排）：
1. 先对每个chunk执行技能一（主译）。单chunk一次；多chunk时每个chunk一次
   （串行：前chunk译文作后chunk上下文参考；并行：每chunk独立携带完整策略）。
2. 多chunk时，系统先做确定性预检（Python·零成本）；预检发现不一致才触发技能二。单chunk跳过。
3. {NT_n}/{T_n}占位符原样保留，绝不翻译、修改、删除。

执行规则：
- 每次调用只执行追加的那份技能说明书对应的工作，不得执行另一项技能。
- 技能一直接输出译文MD文本；技能二输出修复后的完整译文。不要添加解释、不要用代码块包裹整篇译文。
"""


# ══════════════════════════════════════════════════════════════════
# 工作流协调器（按工作流调用skill·代码固定顺序）
# ══════════════════════════════════════════════════════════════════

async def spawn_translate(
    chunks: list[Chunk],
    term_table: TermTable,
    strategy_book: StrategyBook,
    tm_refs: list[TMEntry] | None = None,
    direction: str = "",
) -> TranslateResult:
    """
    译中Sub-Agent主入口（串行工作流）。

    执行顺序（写死在代码里，AI不能跳步）：
      1. 技能一·主译：逐chunk翻译（前chunk译文作后chunk上下文·前2000字符）
      2. 技能二·一致性检查与修复：仅多chunk时触发（Python预检+LLM条件修复）

    Args:
        direction: 兼容参数——翻译方向以策略书 direction 字段为准；
                   仅当策略书未记录（direction为空）时用作兜底，默认 "en_to_zh"
    """
    # 方向单一事实来源=策略书：未记录时用调用参数兜底
    if not strategy_book.direction:
        strategy_book.direction = direction or "en_to_zh"

    chunk_skill = ChunkTranslateSkill(agent_prompt=TRANSLATE_AGENT_SYSTEM_PROMPT)
    consistency_skill = ConsistencySkill(agent_prompt=TRANSLATE_AGENT_SYSTEM_PROMPT)

    # ── Step 1: 逐chunk主译（技能一·串行）──
    draft_parts: list[str] = []
    prev_translation = ""

    for chunk in chunks:
        try:
            translated = await chunk_skill.execute(
                chunk=chunk, term_table=term_table, strategy_book=strategy_book,
                tm_refs=tm_refs, prev_translation=prev_translation,
            )
            draft_parts.append(translated)
            prev_translation = translated
        except Exception as e:
            # chunk失败降级：标记原文保留·不影响其余chunk·前文上下文不更新
            print(f"[TranslateAgent] chunk {chunk.chunk_id} 翻译失败: {e}")
            draft_parts.append(f"[翻译失败] {chunk.source_text[:200]}...")

    # ── Step 2: 合并初译稿 + 一致性检查（技能二·仅多chunk时触发）──
    draft = "\n\n".join(draft_parts) if len(draft_parts) > 1 else (draft_parts[0] if draft_parts else "")
    if len(chunks) > 1:
        draft, consistency_report = await consistency_skill.execute(
            draft_parts, chunks, term_table, strategy_book
        )
    else:
        consistency_report = ConsistencyReport()

    return TranslateResult(
        draft=draft,
        consistency_report=consistency_report,
        tm_refs_used=len(tm_refs) if tm_refs else 0,
    )


async def spawn_translate_parallel(
    chunks: list[Chunk],
    term_table: TermTable,
    strategy_book: StrategyBook,
    tm_refs: list[TMEntry] | None = None,
    direction: str = "",
    max_concurrency: int = 4,
    parent_context: AgentContext | None = None,
) -> TranslateResult:
    """
    并行chunk翻译（D2新增能力·D5技能化后走技能一）。

    与串行模式的区别：
      - 所有chunk同时翻译（无跨chunk上下文传递）
      - 每个chunk独立携带完整策略书+术语表+TM参考（通过技能一的并行模式）
      - 翻译完成后做一致性检查（Python预检+LLM条件修复）

    Args:
        direction: 兼容参数——翻译方向以策略书 direction 字段为准；
                   仅当策略书未记录（direction为空）时用作兜底，默认 "en_to_zh"
        max_concurrency: 最大并发chunk翻译数（默认4，避免API频率限制）
        parent_context: 父级上下文（用于取消信号传递）
    """
    # 方向单一事实来源=策略书：未记录时用调用参数兜底
    if not strategy_book.direction:
        strategy_book.direction = direction or "en_to_zh"

    chunk_skill = ChunkTranslateSkill(agent_prompt=TRANSLATE_AGENT_SYSTEM_PROMPT)
    consistency_skill = ConsistencySkill(agent_prompt=TRANSLATE_AGENT_SYSTEM_PROMPT)

    # ── Step 1: 并行主译（技能一·并行模式·每chunk一个任务）──
    tasks = [
        SpawnTask(
            name=f"translate_{chunk.chunk_id}",
            func=chunk_skill.execute,
            args=(chunk, term_table, strategy_book),
            kwargs={
                "tm_refs": tm_refs,
                "parallel_mode": True,
            },
            context=AgentContext.simple(
                f"ChunkTranslator[{chunk.chunk_id}]",
                timeout=120.0,
            ),
        )
        for chunk in chunks
    ]

    results = await spawn_parallel(tasks, max_concurrency=max_concurrency, parent_context=parent_context)

    # 合并结果（按原chunk顺序）
    draft_parts: list[str] = []
    for i, result in enumerate(results):
        if result.success:
            draft_parts.append(str(result.data))
        else:
            chunk = chunks[i]
            draft_parts.append(f"[翻译失败:{result.error[:80]}] {chunk.source_text[:200]}...")

    # ── Step 2: 合并初译稿 + 一致性检查（技能二·仅多chunk时触发）──
    draft = "\n\n".join(draft_parts) if len(draft_parts) > 1 else (draft_parts[0] if draft_parts else "")
    consistency_report = ConsistencyReport()
    if len(chunks) > 1:
        draft, consistency_report = await consistency_skill.execute(
            draft_parts, chunks, term_table, strategy_book
        )

    return TranslateResult(
        draft=draft,
        consistency_report=consistency_report,
        tm_refs_used=len(tm_refs) if tm_refs else 0,
    )


# ══════════════════════════════════════════════════════════════════
# BaseAgent 封装（D2新增·D5保持）
# ══════════════════════════════════════════════════════════════════

@register_agent
class TranslateAgent(BaseAgent):
    """
    译中Sub-Agent（BaseAgent封装）。

    使用方式：
        # 串行模式（向后兼容）
        result = await spawn_translate(chunks, term_table, strategy_book, tm_refs)

        # 并行模式（D2新增·多chunk加速）
        result = await spawn_translate_parallel(chunks, term_table, strategy_book, tm_refs)

        # 通过 BaseAgent.run()（统一框架·自动选择模式）
        agent = TranslateAgent(context=AgentContext.simple("TranslateAgent", timeout=300))
        agent.parallel_mode = True  # 启用并行chunk翻译
        agent.max_concurrency = 4
        result = await agent.run(chunks, term_table, strategy_book, tm_refs)
    """

    def __init__(self, context: AgentContext | None = None):
        super().__init__(context)
        self.parallel_mode: bool = False        # 是否使用并行chunk翻译
        self.max_concurrency: int = 4            # 并行chunk最大并发数

    @property
    def agent_name(self) -> str:
        return "TranslateAgent"

    def default_context(self) -> AgentContext:
        return AgentContext(
            agent_name=self.agent_name,
            timeout_seconds=300.0,  # 多chunk翻译需要更长时间
        )

    async def execute(
        self,
        chunks: list[Chunk],
        term_table: TermTable,
        strategy_book: StrategyBook,
        tm_refs: list[TMEntry] | None = None,
        direction: str = "en_to_zh",
    ) -> TranslateResult:
        """执行译中流程。根据parallel_mode选择串行/并行。"""
        if self.parallel_mode and len(chunks) > 1:
            print(f"[TranslateAgent] 并行模式：{len(chunks)} chunks, max_concurrency={self.max_concurrency}")
            return await spawn_translate_parallel(
                chunks, term_table, strategy_book, tm_refs,
                direction=direction,
                max_concurrency=self.max_concurrency,
                parent_context=self.context,
            )
        else:
            return await spawn_translate(
                chunks, term_table, strategy_book, tm_refs,
                direction=direction,
            )
