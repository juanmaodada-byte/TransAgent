"""
技能三：术语翻译（译前）·实现脚本
===============================
Vibe Coder A | v1.0 | 2026-08-19 (D7·术语提取/翻译拆分)

职责：对已提取的术语确定目标语言译法——RAG匹配优先（确定性），未命中LLM批量兜底。

输入：术语候选列表 [{"term", "context"}] + 策略书 + 用户偏好
输出：TermTable（entries + pending + 统计）

流程：
  ① search_rag_batch 整批匹配（一次嵌入 + 一次Chroma查询）
     - 命中 high/medium → 复用库中译法（source=RAG命中）
     - 命中 low / 未命中  → 进LLM兜底
  ② LLM批量翻译未命中（附上下文消歧·分小批·每批一次调用）
     - 译法 → entries（medium·供待确认收集）

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + knowledge.rag_terms
不依赖：技能一/技能二（agent说明书由工作流实例化时注入）→ 无循环导入
"""

import asyncio

from transagent.interface import (
    TermTable,
    TermEntry,
    Confidence,
    TermSource,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class TermTranslationSkill(Skill):
    """技能三：术语翻译。输入 术语候选+策略书+偏好 → 输出 TermTable（RAG优先·LLM兜底）。"""
    name = "term_translation"
    description = "术语翻译：RAG匹配优先，未命中LLM批量兜底，产出术语表"
    skill_dir = "pre_skills/term_translation"
    temperature = 0.2
    max_tokens = 8000  # D9.1：推理型模型思考耗token·过小→空响应
    json_mode = True
    requires = {"term_candidates", "strategy_book", "user_prefs"}  # D6共享池
    provides = {"term_table"}                                      # D6共享池

    # LLM批量翻译的单批上限（防止一批过大触发json_mode空响应）
    LLM_BATCH = 25

    async def execute(
        self,
        terms: list[str],
        strategy,
        user_prefs,
    ) -> TermTable:
        """执行术语翻译。

        Args:
            terms: 术语候选 list[str]（裸术语名·提取技能产出）
            strategy: 策略书（读取 direction 路由方向 + ict_domain 消歧）
            user_prefs: 用户偏好（user_id 关联 RAG）
        """
        table = TermTable()
        if not terms:
            return table

        term_texts = [str(t).strip() for t in terms if str(t).strip()]

        # ── ① RAG整批匹配（一次嵌入+一次查询·确定性）──
        from transagent.backend.knowledge.rag_terms import search_rag_batch
        try:
            batch = await asyncio.to_thread(
                search_rag_batch, term_texts, user_prefs.user_id, strategy.ict_domain)
        except Exception as e:
            print(f"[TermTranslationSkill] RAG批量匹配失败: {e}")
            batch = [[] for _ in term_texts]

        llm_misses: list[str] = []
        for term, hits in zip(term_texts, batch):
            if hits and hits[0].confidence != Confidence.LOW.value:
                best = hits[0]
                table.entries.append(TermEntry(
                    term=term,
                    translation=best.translation,
                    domain=strategy.ict_domain,
                    confidence=best.confidence,   # 采信检索端诚实置信度
                    action=best.action,
                    source=TermSource.RAG_HIT.value,
                    user_id=user_prefs.user_id,
                ))
                table.rag_hit_count += 1
            else:
                if hits:
                    print(f"[TermTranslationSkill] RAG弱匹配不采信({term}→{hits[0].term}, "
                          f"{hits[0].confidence})")
                llm_misses.append(term)

        # ── ② LLM批量翻译未命中（分小批）──
        if llm_misses:
            for i in range(0, len(llm_misses), self.LLM_BATCH):
                sub = llm_misses[i:i + self.LLM_BATCH]
                t = await self._translate_by_llm(sub, strategy, user_prefs)
                table.entries.extend(t.entries)
                table.pending_entries.extend(t.pending_entries)

        # ── 去重 + 统计 ──
        table.entries = _dedup_terms(table.entries)
        table.pending_entries = _dedup_terms(table.pending_entries)
        table.total_count = len(table.entries) + len(table.pending_entries)
        table.llm_gen_count = sum(
            1 for e in (table.entries + table.pending_entries)
            if e.source == TermSource.LLM_GEN.value
        )
        return table

    async def _translate_by_llm(
        self, misses: list[str], strategy, user_prefs,
    ) -> TermTable:
        """LLM批量翻译一小批术语（裸术语·依赖领域标签消歧）。"""
        direction = strategy.direction or "en_to_zh"
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        lines = "\n".join(f"- {m}" for m in misses)
        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy.ict_domain}

待翻译术语：
{lines}"""

        table = TermTable()
        try:
            result = await chat(
                self.full_system_prompt(),
                user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=self.json_mode,
            )
            if not isinstance(result, dict):
                return table
            for tr in (result.get("translations", []) or []):
                term = str(tr.get("term", "")).strip()
                translation = str(tr.get("translation", "")).strip()
                if not term or not translation:
                    continue
                action = "notranslate" if translation == term else "translate"
                table.entries.append(TermEntry(
                    term=term,
                    translation=translation,
                    domain=strategy.ict_domain,
                    confidence=Confidence.MEDIUM.value,  # LLM生成·待确认
                    action=action,
                    source=TermSource.LLM_GEN.value,
                    user_id=user_prefs.user_id,
                ))
        except Exception as e:
            print(f"[TermTranslationSkill] LLM批量翻译失败: {e}")

        return table


def _dedup_terms(entries: list[TermEntry]) -> list[TermEntry]:
    """按term去重（保留第一条），忽略空term。"""
    seen: set[str] = set()
    deduped: list[TermEntry] = []
    for e in entries:
        if e.term and e.term not in seen:
            seen.add(e.term)
            deduped.append(e)
    return deduped
