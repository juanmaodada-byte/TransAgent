"""
技能二：术语提取（译前）·实现脚本
=================================
Vibe Coder A | v1.2 | 2026-08-12 (D5·输出规范化)

说明书：本目录上级的 skill.md（由Skill基类运行时加载）。
本文件只放实现：组装用户消息 → 调用LLM → 解析/分流/去重为单批术语表。

输入：方向（以策略书direction字段为准）+ 领域标签 + 文本（全文或某个chunk）
输出：单批术语表（多chunk时由工作流多次调用本技能，再合并去重）

v1.2 输出规范化（稳定输出契约）：
  - LLM每条术语只输出三字段：term / translation / source（来源=RAG命中|Web搜索|LLM生成）
  - domain / confidence / action 由系统派生，不再信任LLM自由填写：
      domain      = 策略书领域标签（消除逐术语domain与策略书冲突）
      confidence  = 按分流位置派生（term_table→medium·RAG命中→high·pending→low）
      action      = 译法=原文 → notranslate，否则 translate
  - source 诚实化：LLM声称的来源不可信（它无法真实检索），统一标 LLM生成；
    真实来源（RAG命中/未来Web搜索）由代码在查证环节直接构造TermEntry覆盖

D5说明：
  - RAG查证逻辑（_apply_rag_verification）随技能迁入本文件，
    按配置开关启用（cfg.rag_verification_enabled·D6整合后默认True）
  - 解析助手（_to_entry/_dedup）只属于本技能，不与其他技能共享

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + config
不依赖：技能一、译前agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import asyncio
from typing import Optional

from transagent.interface import (
    UserPrefs,
    StrategyBook,
    TermTable,
    TermEntry,
    Confidence,
    TermAction,
    TermSource,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class TermExtractionSkill(Skill):
    """
    技能二：术语提取。输入 方向+领域标签+文本（全文或某个chunk）→ 输出单批术语表。

    方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en），
    只执行 skill.md 中对应方向的提取标准。

    单批语义：一次调用处理"本次提供的文本"。多chunk时由工作流多次调用本技能，
    再合并去重——本技能不感知其他批次，也不需要感知（合并是协调器的职责）。
    """
    name = "term_extraction"
    description = "从文本中提取术语并定译法，产出术语表（单批）"
    skill_dir = "pre_skills/term_extraction"
    temperature = 0.2
    max_tokens = 4000
    json_mode = True

    async def execute(
        self,
        fragment: str,
        strategy: StrategyBook,
        user_prefs: UserPrefs,
        part_label: str = "全文",
    ) -> TermTable:
        """执行一次术语提取。

        Args:
            fragment: 本次提供的文本（全文 或 单个chunk）
            strategy: 策略书（本技能读取其 direction 字段路由方向）
            part_label: 本次是第几部分（如"第2/3部分"），写入用户消息供LLM感知
        """
        direction = strategy.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy.ict_domain}
本次处理文本：{part_label}

待提取术语的文本：
{fragment}"""

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

            raw_terms = list(result.get("term_table", []) or [])
            raw_pending = list(result.get("pending_terms", []) or [])

            # ── RAG查证（按配置启用·成员C完成后打开开关）──
            # 命中的术语已直接进入 table.entries（high·RAG命中），并从 raw_terms 剔除。
            cfg = get_config().pipeline
            if getattr(cfg, "rag_verification_enabled", False):
                await _apply_rag_verification(raw_terms, table,
                                              user_prefs.user_id,
                                              strategy.ict_domain)

            # ── LLM置信度分流：high/medium → entries，low → pending ──
            for t in raw_terms:
                entry = _to_entry(t, strategy.ict_domain, user_prefs.user_id)
                if entry:
                    if entry.confidence == Confidence.LOW.value:
                        table.pending_entries.append(entry)
                    else:
                        table.entries.append(entry)

            # ── LLM明确标记的低置信度术语（pending字段）──
            for t in raw_pending:
                entry = _to_entry(t,
                                  strategy.ict_domain,
                                  user_prefs.user_id,
                                  force_pending=True)
                if entry:
                    table.pending_entries.append(entry)

            # ── 批内去重（同一术语本批内只保留一条）──
            table.entries = _dedup(table.entries)
            table.pending_entries = _dedup(table.pending_entries)
        except Exception as e:
            print(f"[TermExtractionSkill] 术语批次失败: {e}")

        return table


async def _apply_rag_verification(
    raw_terms: list[dict],
    table: TermTable,
    user_id: str,
    domain_label: str,
) -> None:
    """
    RAG术语库查证（按配置开关启用）。

    对每个候选术语查RAG语义检索（携带领域标签消歧），采信门槛：
      - high 命中（相似度≥0.95·全称直查/近似全等）→ 复用库中译法，加入 entries
      - medium 命中（相似度0.80~0.95·语义近似）→ 复用库中译法，保留诚实置信度
      - low 命中（相似度0.70~0.80·近义弱匹配）→ 不采信：保留在候选列表走LLM置信度分流，
        避免检索端已判为弱匹配的错误译法（如 authentication→加密）被强制HIGH注入术语表
    - 未命中 → 保留在候选列表，走LLM置信度分流

    同步查询通过 asyncio.to_thread 放到线程池，避免 bge-m3 首次加载阻塞事件循环。
    """
    from transagent.backend.knowledge.rag_terms import search_rag

    async def _check(term_text: str) -> list:
        return await asyncio.to_thread(search_rag, term_text, user_id,
                                       domain_label)

    hit_terms: set[str] = set()
    for t in raw_terms:
        term_text = str(t.get("term", "")).strip()
        if not term_text:
            continue
        try:
            rag_results = await _check(term_text)
        except Exception as e:
            print(f"[TermExtractionSkill] RAG查证失败({term_text}): {e}")
            continue
        if rag_results:
            best = rag_results[0]
            if best.confidence == Confidence.LOW.value:
                # 弱匹配：检索端已按相似度降级为 low → 不采信，退回LLM分流
                print(f"[TermExtractionSkill] RAG弱匹配不采信({term_text}→{best.term}, "
                      f"置信度{best.confidence})")
                continue
            table.entries.append(TermEntry(
                term=term_text,
                translation=best.translation,
                domain=domain_label,
                confidence=best.confidence,   # 采信检索端诚实置信度(high/medium)，不再强制HIGH
                action=best.action,
                source=TermSource.RAG_HIT.value,
                user_id=user_id,
            ))
            table.rag_hit_count += 1
            hit_terms.add(term_text)

    # 剔除已命中的候选（外层继续LLM分流）
    if hit_terms:
        raw_terms[:] = [t for t in raw_terms
                        if str(t.get("term", "")).strip() not in hit_terms]


def _to_entry(t: dict, domain: str, user_id: str,
              force_pending: bool = False) -> Optional[TermEntry]:
    """LLM返回的原始dict（三字段契约：term/translation/source）→ TermEntry。

    派生规则（减少LLM自由填写带来的输出不稳定）：
      - domain     ：取自策略书领域标签（忽略LLM逐术语填写的domain·避免与策略书冲突）
      - source     ：诚实化——LLM声称的来源不采信（它无法真实检索），统一标 LLM生成；
                     真实来源（RAG命中/未来Web搜索）由代码在查证环节直接构造TermEntry覆盖
      - confidence ：按分流位置派生——pending→low；entries→RAG命中为high、其余medium
      - action     ：译法与原文一致（保留原文不译）→ notranslate，否则 translate
      - 空译法     ：降级为 pending（低置信·待用户确认），避免空译法静默入库
    """
    term_text = str(t.get("term", "")).strip()
    if not term_text:
        return None
    translation = str(t.get("translation", "")).strip()
    if not translation:
        force_pending = True   # 无译法 → 待确认，不静默入库
    source = TermSource.LLM_GEN.value      # 诚实化：无RAG/搜索时统一LLM生成
    if force_pending:
        confidence = Confidence.LOW.value
    elif source == TermSource.RAG_HIT.value:
        confidence = Confidence.HIGH.value
    else:
        confidence = Confidence.MEDIUM.value
    action = (
        TermAction.NOTRANSLATE.value
        if translation and translation.lower() == term_text.lower()
        else TermAction.TRANSLATE.value
    )
    return TermEntry(
        term=term_text,
        translation=translation,
        domain=domain,
        confidence=confidence,
        action=action,
        source=source,
        user_id=user_id,
    )


def _dedup(entries: list[TermEntry]) -> list[TermEntry]:
    """按term去重（保留第一条），忽略空term。"""
    seen: set[str] = set()
    deduped: list[TermEntry] = []
    for e in entries:
        if e.term and e.term not in seen:
            seen.add(e.term)
            deduped.append(e)
    return deduped
