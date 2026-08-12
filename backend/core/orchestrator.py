"""
主Agent编排器
=============
Vibe Coder A | v1.1 | 2026-08-07 (D2)

职责：管控翻译全流程，委托翻译核心工作给三个Sub-Agent。
      管理会话状态、知识库读写、交付层调度。

D2更新：集成agent_framework，使用统一spawn接口获得超时/重试/计时能力。

用法：
    orchestrator = Orchestrator(user_id="user_001")
    session = await orchestrator.translate(
        file_path="/path/to/doc.docx",
        on_progress=lambda step, state, msg: print(f"[{step}] {msg}"),
    )
"""

import asyncio
import time
from transagent.interface import (
    TranslationSession, StepState, DegradationLevel,
    PreprocessResult, PreTranslateResult, TranslateResult, PostTranslateResult,
    UserPrefs, EvolutionReport, AlignedPair,
)
from transagent.backend.config import get_config
from transagent.backend.pipeline.preprocess import preprocess
from transagent.backend.pipeline.restore import restore_placeholders
from transagent.backend.pipeline.aligner import align_sentences
from transagent.backend.core.pre_agent import spawn_pre_translate
from transagent.backend.core.translate_agent import spawn_translate, spawn_translate_parallel
from transagent.backend.core.post_agent import spawn_post_translate
from transagent.backend.core.degradation import handle_degradation
from transagent.backend.knowledge.rag_terms import write_rag_terms
from transagent.backend.knowledge.tm_store import write_tm_entries, search_tm
from transagent.backend.knowledge.user_prefs import load_user_prefs, save_user_prefs
from transagent.backend.core.agent_framework import (
    AgentContext, AgentResult, spawn,
)


class Orchestrator:
    """主Agent编排器"""

    def __init__(self, user_id: str, workspace_dir: str | None = None):
        self.user_id = user_id
        self.workspace_dir = workspace_dir or get_config().app.workspace_dir
        # D2新增：并行chunk翻译配置
        self.parallel_chunks: bool = False     # 是否启用并行chunk翻译
        self.max_chunk_concurrency: int = 4    # 并行chunk最大并发数

    async def translate(
        self,
        file_path: str,
        on_progress=None,           # Callable[[str, StepState, str], None]
        on_terms_pending=None,      # async/sync Callable[[session, list], list] → 用户确认后的术语
    ) -> TranslationSession:
        """执行完整翻译流程。

        D2新增：可通过 Orchestrator.parallel_chunks = True 启用并行chunk翻译，
        显著加速长文档（5+ chunks）的翻译过程。
        术语确认：on_terms_pending 支持 async 与 sync 两种回调，
        签名统一为 (session, pending_terms) -> list[TermEntry]。
        """

        session = TranslationSession(
            user_id=self.user_id,
            file_path=file_path,
        )
        session.started_at = time.time()

        def _progress(step: str, state: StepState, msg: str):
            session.steps[step] = state
            if on_progress:
                on_progress(step, state, msg)

        try:
            # ── Step 1: 预处理（Vibe Coder B）──
            await self._step_preprocess(session, _progress)

            # ── Step 2: 译前 Sub-Agent ──
            await self._step_pre_translate(session, _progress)

            # ── ⏸️ 术语确认断点 ──
            if session.pending_terms:
                await self._step_terminology_confirm(session, _progress, on_terms_pending)

            # ── Step 3: 译中 Sub-Agent ──
            await self._step_translate(session, _progress)

            # ── Step 4: 译后 Sub-Agent ──
            await self._step_post_translate(session, _progress)

            # ── Step 5: 交付 + 学习 ──
            await self._step_deliver_and_learn(session, _progress)

            session.completed_at = time.time()
            _progress("export", StepState.COMPLETED,
                      f"翻译完成，耗时 {session.elapsed_seconds():.0f}秒")

        except Exception as e:
            session.errors.append(str(e))
            await handle_degradation(session, e, _progress)

        return session

    # ── Step 1: 预处理 ─────────────────────────────────────────────

    async def _step_preprocess(self, session: TranslationSession, progress):
        progress("input_detect", StepState.IN_PROGRESS, "正在检测文件格式…")

        # 加载用户偏好
        try:
            session.user_prefs = load_user_prefs(self.user_id)
        except Exception:
            session.user_prefs = UserPrefs(user_id=self.user_id)

        # 一站式预处理
        progress("input_convert", StepState.IN_PROGRESS, "正在转换文档格式…")
        try:
            result = preprocess(session.file_path)
            session.preprocess_result = result
            progress("input_convert", StepState.COMPLETED,
                     f"预处理完成: {result.token_estimate_total} tokens | "
                     f"{result.chunk_count} chunk{'s' if result.chunk_count > 1 else ''} | "
                     f"占位符 {result.placeholder_map.nt_count if result.placeholder_map else 0}处")
        except Exception as e:
            progress("input_convert", StepState.FAILED, f"预处理失败: {e}")
            raise

    # ── Step 2: 译前 ───────────────────────────────────────────────

    async def _step_pre_translate(self, session: TranslationSession, progress):
        progress("pre_translate", StepState.IN_PROGRESS, "译前Sub-Agent工作中（策略+术语）…")

        # D2：使用框架spawn包装
        agent_ctx = AgentContext(
            agent_name="PreTranslateAgent",
            timeout_seconds=180.0,
            parent_session_id=session.session_id,
        )

        try:
            result: AgentResult = await spawn(
                spawn_pre_translate,
                session.preprocess_result,
                session.user_prefs,
                context=agent_ctx,
            )

            if not result.success:
                raise RuntimeError(f"PreTranslateAgent failed: {result.error}")

            session.pre_translate_result = result.data
            pre_result = result.data

            # ── 待确认术语：低置信度 + 中置信度的 LLM 生成术语 ──
            # 术语技能把 LLM 生成的译法标为 medium（RAG命中/白名单 → high），
            # 仅 low 触发确认会让大多数文档直接跳过确认环节。
            # 为保障"人机交互确认"的体验，medium 的 LLM 生成术语也纳入待确认。
            session.pending_terms = self._collect_pending_terms(pre_result.term_table)

            domain = pre_result.strategy_book.ict_domain if pre_result.strategy_book else "未知"
            term_count = pre_result.term_table.total_count if pre_result.term_table else 0
            pending = len(session.pending_terms)

            msg = f"ICT子领域: {domain} | 术语: {term_count}个 ({result.elapsed_seconds:.1f}s)"
            if pending:
                msg += f"（{pending}个待确认）"
            else:
                msg += "（全部自动接受）"
            progress("pre_translate", StepState.COMPLETED, msg)

        except Exception as e:
            progress("pre_translate", StepState.FAILED, f"译前失败: {e}")
            session.degradation_level = DegradationLevel.L2
            raise

    # ── ⏸️ 术语确认断点 ────────────────────────────────────────────

    async def _step_terminology_confirm(self, session, progress, on_terms_pending):
        progress("terminology_confirm", StepState.WAITING_USER,
                 f"{len(session.pending_terms)}个术语需要确认")

        if on_terms_pending:
            # 支持 async 与 sync 两种回调，签名统一为 (session, pending_terms)
            confirmed = await self._invoke_terms_callback(on_terms_pending, session)
            if session.pre_translate_result and session.pre_translate_result.term_table:
                tt = session.pre_translate_result.term_table
                # 确认结果合并回术语表（按 term 去重：medium 术语已在 entries 中）
                existing_idx = {e.term: i for i, e in enumerate(tt.entries)}
                for term in confirmed:
                    term.source = "用户确认"
                    term.confidence = "high"
                    if term.term in existing_idx:
                        tt.entries[existing_idx[term.term]] = term
                    else:
                        tt.entries.append(term)
                # 清空低置信度待确认列表（已合并进 entries，避免重复计数）
                tt.pending_entries = []
            progress("terminology_confirm", StepState.COMPLETED,
                     f"用户确认{len(confirmed)}个术语")
        else:
            # Demo模式：自动接受低置信度术语
            if session.pre_translate_result and session.pre_translate_result.term_table:
                tt = session.pre_translate_result.term_table
                # 自动接受：合并去重（medium 术语可能已在 entries 中）
                existing_idx = {e.term: i for i, e in enumerate(tt.entries)}
                for term in session.pending_terms:
                    term.source = "自动接受"
                    if term.term in existing_idx:
                        tt.entries[existing_idx[term.term]] = term
                    else:
                        tt.entries.append(term)
                tt.pending_entries = []
            progress("terminology_confirm", StepState.COMPLETED,
                     f"自动接受{len(session.pending_terms)}个术语")

        session.pending_terms = []

    @staticmethod
    async def _invoke_terms_callback(cb, session):
        """调用术语确认回调，兼容 async/sync 两种实现。"""
        if asyncio.iscoroutinefunction(cb):
            return await cb(session, session.pending_terms)
        return cb(session, session.pending_terms)

    @staticmethod
    def _collect_pending_terms(term_table) -> list:
        """
        收集全部提取术语作为「待确认」列表（按 term 去重）。

        需求：所有提取到的术语都要出现在确认环节，供用户确认/修改译法，
        而不只是中低置信度术语。RAG命中/白名单的高置信度术语同样展示，
        便于用户核对并沉淀为「用户确认」来源。
        """
        if not term_table:
            return []
        combined = list(term_table.pending_entries) + list(term_table.entries)
        seen: set = set()
        pending: list = []
        for e in combined:
            if e.term and e.term not in seen:
                seen.add(e.term)
                pending.append(e)
        return pending

    # ── Step 3: 译中 ───────────────────────────────────────────────

    async def _step_translate(self, session: TranslationSession, progress):
        chunk_count = len(session.preprocess_result.chunks) if session.preprocess_result else 0
        mode_label = "并行" if self.parallel_chunks and chunk_count > 1 else "串行"
        progress("translate", StepState.IN_PROGRESS,
                 f"译中Sub-Agent工作中（{mode_label}·{chunk_count} chunk）…")

        # TM搜索
        tm_refs = []
        try:
            full_source = session.preprocess_result.protected_md
            from transagent.backend.knowledge.tm_store import search_tm
            tm_refs = search_tm(full_source, self.user_id)
        except Exception:
            pass

        try:
            # D2：使用框架spawn包装，获得超时保护+计时
            agent_ctx = AgentContext(
                agent_name="TranslateAgent",
                timeout_seconds=300.0,
                parent_session_id=session.session_id,
            )

            if self.parallel_chunks and chunk_count > 1:
                # 并行chunk翻译（D2新增能力·方向以策略书direction字段为准）
                result: AgentResult = await spawn(
                    spawn_translate_parallel,
                    session.preprocess_result.chunks,
                    session.pre_translate_result.term_table,
                    session.pre_translate_result.strategy_book,
                    tm_refs,
                    max_concurrency=self.max_chunk_concurrency,
                    parent_context=agent_ctx,
                    context=agent_ctx,
                )
            else:
                # 串行chunk翻译（传统模式）
                result: AgentResult = await spawn(
                    spawn_translate,
                    session.preprocess_result.chunks,
                    session.pre_translate_result.term_table,
                    session.pre_translate_result.strategy_book,
                    tm_refs,
                    context=agent_ctx,
                )

            if not result.success:
                raise RuntimeError(f"TranslateAgent failed: {result.error}")

            session.translate_result = result.data
            tr = result.data
            cr = tr.consistency_report
            msg = f"初译完成: {len(tr.draft)}字符 ({result.elapsed_seconds:.1f}s)"
            if cr:
                if cr.precheck_passed:
                    msg += " | 一致性: 预检通过"
                else:
                    msg += f" | 一致性: 修复{cr.issues_found}处不一致"
            progress("translate", StepState.COMPLETED, msg)

        except Exception as e:
            progress("translate", StepState.FAILED, f"翻译失败: {e}")
            session.degradation_level = DegradationLevel.L2
            raise

    # ── Step 4: 译后 ───────────────────────────────────────────────

    async def _step_post_translate(self, session: TranslationSession, progress):
        progress("post_translate", StepState.IN_PROGRESS, "译后Sub-Agent工作中（质检→润色）…")

        # D2：使用框架spawn包装
        agent_ctx = AgentContext(
            agent_name="PostTranslateAgent",
            timeout_seconds=180.0,
            parent_session_id=session.session_id,
        )

        try:
            result: AgentResult = await spawn(
                spawn_post_translate,
                session.preprocess_result.protected_md,
                session.translate_result.draft,
                session.pre_translate_result.term_table,
                session.pre_translate_result.strategy_book,
                context=agent_ctx,
            )

            if not result.success:
                raise RuntimeError(f"PostTranslateAgent failed: {result.error}")

            session.post_translate_result = result.data
            post_result = result.data

            if post_result.qa_report:
                qa = post_result.qa_report
                progress("post_translate", StepState.COMPLETED,
                         f"质检: {qa.total_score:.1f}分 ({result.elapsed_seconds:.1f}s) | "
                         f"术语{qa.term_accuracy}·语义{qa.semantic_fidelity}·"
                         f"代码{qa.code_integrity}·流畅{qa.fluency}·风格{qa.style_match}")
            else:
                progress("post_translate", StepState.COMPLETED,
                         f"译后完成 ({result.elapsed_seconds:.1f}s)")

        except Exception as e:
            # 降级：交付初译稿
            session.degradation_level = DegradationLevel.L1
            session.errors.append(f"post_translate: {e}")
            session.post_translate_result = PostTranslateResult(
                final_text=session.translate_result.draft,
                polish_notes=f"译后失败·交付初译稿: {e}",
            )
            progress("post_translate", StepState.COMPLETED, f"译后降级·交付初译稿")

    # ── Step 5: 交付 + 学习 ────────────────────────────────────────

    async def _step_deliver_and_learn(self, session: TranslationSession, progress):
        # 5a. 占位符还原
        progress("restore", StepState.IN_PROGRESS, "正在还原不可译区域…")
        try:
            pmap = session.preprocess_result.placeholder_map
            if pmap:
                restored = restore_placeholders(session.post_translate_result.final_text, pmap)
                session.final_text_restored = restored
                progress("restore", StepState.COMPLETED,
                         f"还原{pmap.nt_count + pmap.t_count}处占位符")
            else:
                session.final_text_restored = session.post_translate_result.final_text
                progress("restore", StepState.SKIPPED, "无占位符")
        except Exception as e:
            session.errors.append(f"restore: {e}")
            session.final_text_restored = session.post_translate_result.final_text

        # 5b. 句级对齐
        progress("align", StepState.IN_PROGRESS, "正在句级对齐…")
        try:
            session.aligned_pairs = align_sentences(
                session.preprocess_result.protected_md,
                session.final_text_restored,
            )
            progress("align", StepState.COMPLETED, f"对齐{len(session.aligned_pairs)}个句对")
        except Exception as e:
            session.errors.append(f"align: {e}")
            progress("align", StepState.FAILED, f"对齐失败: {e}")

        # 5c. 学习层写入
        progress("learn", StepState.IN_PROGRESS, "正在更新知识库…")
        await self._write_knowledge(session, progress)

    async def _write_knowledge(self, session: TranslationSession, progress):
        """写入RAG + TM（失败不影响交付）"""
        domain = (session.pre_translate_result.strategy_book.ict_domain
                  if session.pre_translate_result and session.pre_translate_result.strategy_book
                  else "")

        # RAG术语写入
        terms_to_write = []
        if session.pre_translate_result and session.pre_translate_result.term_table:
            for t in session.pre_translate_result.term_table.entries:
                if t.source in ("用户确认", "RAG命中") and t.confidence == "high":
                    t.user_id = self.user_id
                    t.domain = domain
                    terms_to_write.append(t)

        # TM写入（质检≥8.5分的句对）
        tm_entries = []
        if (session.post_translate_result and session.post_translate_result.qa_report
                and session.aligned_pairs):
            qa_score = session.post_translate_result.qa_report.total_score
            if qa_score >= 8.5:
                for pair in session.aligned_pairs:
                    from transagent.interface import TMEntry
                    tm_entries.append(TMEntry(
                        source_seg=pair.source_seg if hasattr(pair, 'source_seg') else pair.get("source_seg", ""),
                        target_seg=pair.target_seg if hasattr(pair, 'target_seg') else pair.get("target_seg", ""),
                        quality_score=qa_score,
                        domain=domain,
                        user_id=self.user_id,
                    ))

        new_terms = 0
        new_tm = 0
        if terms_to_write:
            new_terms = write_rag_terms(terms_to_write)
        if tm_entries:
            new_tm = write_tm_entries(tm_entries)

        # 进化报告
        from transagent.backend.knowledge.rag_terms import get_term_count
        from transagent.backend.knowledge.tm_store import get_tm_count

        total_terms = get_term_count(self.user_id)
        total_tm = get_tm_count(self.user_id)
        tm_used = session.translate_result.tm_refs_used if session.translate_result else 0

        session.evolution_report = EvolutionReport(
            new_terms_count=new_terms,
            new_tm_count=new_tm,
            total_terms=total_terms,
            total_tm=total_tm,
            tm_reuse_rate=tm_used / len(tm_entries) if tm_entries else 0,
            rag_hit_rate=(session.pre_translate_result.term_table.rag_hit_count /
                          session.pre_translate_result.term_table.total_count
                          if session.pre_translate_result and session.pre_translate_result.term_table
                          and session.pre_translate_result.term_table.total_count > 0
                          else 0),
            summary=f"本次新增{new_terms}个术语·{new_tm}条TM | 累计术语{total_terms}·TM{total_tm}",
        )

        progress("learn", StepState.COMPLETED,
                 f"新增术语+{new_terms} · TM+{new_tm}")


async def translate_document(
    file_path: str,
    user_id: str = "demo_user",
    on_progress=None,
    on_terms_pending=None,
) -> TranslationSession:
    """一键翻译文档。便捷入口。"""
    orchestrator = Orchestrator(user_id)
    return await orchestrator.translate(file_path, on_progress, on_terms_pending)
