"""
共享池 SharedPool
================
Vibe Coder A | v1.0 | 2026-08-15 (D6)

职责：翻译流程中所有 Agent / 技能产出与消费的统一数据通道。
      每个 Agent 干完活把结果写进池子，下游技能按 requires 声明从中取用。

设计原则：
  - 类型化：每个 artifact 是强类型字段（非自由 dict），IDE友好、可读、可审计
  - per-session：一次翻译一个池子，绝不跨会话共享
  - 可用性校验：技能执行前 validate_pool 检查 requires 齐不齐，缺则报错拦住（不静默瞎猜）
  - 审计：providers / consumers 记录"谁产出·谁消费"，数据流可追溯

使用示例：
    pool = SharedPool(session_id="abc123")
    pool.preprocess_result = preprocess_result          # 编排器从预处理结果解包填入
    pool.source_md = preprocess_result.protected_md
    pool.chunks = preprocess_result.chunks
    pool.placeholder_map = preprocess_result.placeholder_map
    pool.user_prefs = user_prefs
    await spawn(spawn_pre_translate, pool, context=ctx)   # 写 strategy_book / term_table
    await spawn(spawn_translate, pool, context=ctx)       # 写 chunk_drafts / draft / ...
    pool.aligned_pairs = align_chunks(pool.chunks, pool.chunk_drafts)  # 初译稿对齐·进池子即做
    await spawn(spawn_post_translate, pool, context=ctx)  # 读对齐句对定位 → 写 qa_report / final_text

与技能的关系：
    每个技能类声明 requires（从池子拿什么）/ provides（放回池子什么），
    由 agent 工作流在 execute 前调用 skill.validate_pool(pool) 校验。
"""

from dataclasses import dataclass, field
from transagent.interface import (
    PreprocessResult, PlaceholderMap, UserPrefs, StrategyBook, TermTable,
    TMEntry, ConsistencyReport, AlignedPair, QAResult,
)


@dataclass
class SharedPool:
    """一次翻译会话的共享数据池（强类型·per-session）。"""
    session_id: str = ""

    # ── 上游（主agent预处理产出 → 译前消费）──
    # source_md / chunks / placeholder_map 由编排器从 preprocess_result 解包填入；
    # legacy 兼容路径（spawn_* 直接传旧参数）由派发器直接填入。
    preprocess_result: PreprocessResult | None = None
    source_md: str = ""                                   # 受保护源文全文（含占位符）
    chunks: list = field(default_factory=list)            # list[Chunk]
    placeholder_map: PlaceholderMap | None = None         # 质检代码完整性核对用
    user_prefs: UserPrefs | None = None

    # ── 译前 ──
    strategy_book: StrategyBook | None = None
    term_candidates: list = field(default_factory=list)   # 术语提取产物：list[str] 术语名
    term_table: TermTable | None = None

    # ── 译中 ──
    tm_refs: list[TMEntry] = field(default_factory=list)      # TM参考
    chunk_drafts: list[str] = field(default_factory=list)     # 逐chunk译文
    draft: str = ""                                           # 合并后的初译稿
    consistency_report: ConsistencyReport | None = None

    # ── 对齐（初译稿对齐·供质检结构化定位）──
    aligned_pairs: list[AlignedPair] = field(default_factory=list)

    # ── 译后 ──
    qa_report: QAResult | None = None
    final_text: str = ""
    polish_notes: str = ""

    # ── 审计 ──
    providers: dict = field(default_factory=dict)    # {artifact: set(producer)}
    consumers: dict = field(default_factory=dict)    # {artifact: set(consumer)}

    # ── 可用性校验 ──
    def check_missing(self, names) -> list:
        """返回 names 中缺失的 artifact（None / 空串 / 空列表 / 空dict 视为缺失）。"""
        missing = []
        for n in names:
            v = getattr(self, n, None)
            if v is None or v == "" or v == [] or v == {}:
                missing.append(n)
        return missing

    def require(self, *names) -> None:
        """执行前断言：缺数据直接抛错（可用于 agent 层强校验）。"""
        missing = self.check_missing(set(names))
        if missing:
            raise RuntimeError(f"[SharedPool] 缺数据: {missing}")

    # ── 审计登记 ──
    def mark_provided(self, names, agent: str) -> None:
        """登记 producer（技能执行成功后由 Skill.mark_pool_provided 调用）。"""
        for n in names:
            self.providers.setdefault(n, set()).add(agent)

    def mark_consumed(self, names, agent: str) -> None:
        """登记 consumer（技能校验后由 Skill.validate_pool 调用）。"""
        for n in names:
            self.consumers.setdefault(n, set()).add(agent)
