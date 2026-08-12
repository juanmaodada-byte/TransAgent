"""
技能一：主译（译中）·实现脚本
=============================
Vibe Coder A | v1.2 | 2026-08-10 (D5)

说明书：本目录上级的 skill.md（由Skill基类运行时加载·双方向章节合一）。
本文件只放实现：组装用户消息 → 调用LLM → 返回译文。

方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en），
执行 skill.md 中对应方向的章节——翻译方向不另行传参（单一事实来源=策略书）。

输入：单个chunk + 术语表 + 策略书 + TM参考（+ 前文译文·串行 / 并行标记）
输出：该chunk的译文（MD文本）

降级约定：LLM调用失败时抛出异常，由工作流降级（标记[翻译失败]保留原文）——
"是否降级/如何降级"是协调器的职责，本技能只负责正常路径。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + config
不依赖：技能二、译中agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import json

from transagent.interface import (
    Chunk, TermTable, StrategyBook, TMEntry,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config
from transagent.backend.core.skills.skill import Skill, register_skill


TM_REF_TEMPLATE = """
## 翻译记忆参考（相似句段的已有译法）

以下是从你的个人翻译记忆库中找到的相似句段参考。请保持风格一致，但根据实际上下文灵活调整。

{tm_snippets}

---
"""


@register_skill
class ChunkTranslateSkill(Skill):
    """
    技能一：主译。输入 单个chunk + 术语表 + 策略书 + TM参考 → 输出该chunk译文。

    方向：以策略书 direction 字段路由（skill.md 双方向章节合一）。
    支持两种调用模式（由工作流指定）：
      - 串行（默认）：携带前一chunk译文（前2000字符）作上下文参考
      - 并行：不携带前文，独立携带完整策略+术语表（跨chunk一致性由策略书统一指导）

    LLM失败时抛出异常——降级由工作流负责。
    """
    name = "chunk_translate"
    description = "逐chunk主译：术语按表强制使用·TM作参考·遵循策略书·占位符原样保留"
    skill_dir = "translate_skills/chunk_translate"
    temperature = 0.2
    max_tokens = 4000
    json_mode = False

    async def execute(
        self,
        chunk: Chunk,
        term_table: TermTable,
        strategy_book: StrategyBook,
        tm_refs: list[TMEntry] | None = None,
        prev_translation: str = "",
        parallel_mode: bool = False,
    ) -> str:
        """执行一次主译。

        Args:
            chunk: 待翻译的chunk
            strategy_book: 策略书（本技能读取其 direction 字段路由方向章节）
            prev_translation: 串行模式下的前文译文（前2000字符作上下文参考）
            parallel_mode: 并行模式（不携带前文·独立携带完整策略）

        Returns:
            该chunk的译文（MD文本）

        Raises:
            Exception: LLM调用失败（由工作流降级标记）
        """
        cfg = get_config().pipeline
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由

        if parallel_mode:
            strategy_context = _build_parallel_strategy_context(strategy_book)
            source_block = f"""## 待翻译文本

{chunk.source_text}

注意：本chunk与其他chunk并行翻译。请仅根据策略书和术语表保持风格一致性。
不要依赖前文翻译（不存在）。翻译后不要添加任何解释。"""
        else:
            lang_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
            strategy_context = f"""翻译策略：
- ICT子领域：{strategy_book.ict_domain}
- 难度：{strategy_book.difficulty}
- 风格：{strategy_book.style}
- 直译/意译比例：{strategy_book.literal_ratio}
- 翻译方向：{lang_label}
- 目标读者：{strategy_book.target_audience}
- 规则：{json.dumps(strategy_book.rules, ensure_ascii=False)}
"""
            source_block = f"## 待翻译文本\n\n{chunk.source_text}"
            if prev_translation:
                source_block += f"\n\n## 前文翻译（上下文参考）\n{prev_translation[-2000:]}"

        term_context = _build_term_context(term_table)
        tm_context = _build_tm_context(tm_refs) if tm_refs else ""

        user_message = f"""{strategy_context}

{term_context}

{tm_context}

{source_block}"""

        result = await chat(
            self.full_system_prompt(),
            user_message,
            temperature=cfg.translate_temperature,
            max_tokens=cfg.translate_max_tokens,
        )
        return result if isinstance(result, str) else str(result)


def _build_term_context(term_table: TermTable) -> str:
    """构建术语表prompt片段"""
    if not term_table.entries:
        return "## 项目术语表\n（无预定义术语）"

    lines = ["## 项目术语表（以下术语必须使用指定译法）"]
    for e in term_table.entries:
        action_tag = "【不译】" if e.action == "notranslate" else ""
        lines.append(f"- {e.term} → {e.translation} {action_tag}")
    return "\n".join(lines)


def _build_tm_context(tm_refs: list[TMEntry]) -> str:
    """构建TM参考prompt片段"""
    snippets = []
    for e in tm_refs[:5]:  # 最多5条参考
        snippets.append(f"[{e.similarity:.0%}] '{e.source_seg.strip()[:120]}' → '{e.target_seg.strip()[:120]}'")
    return TM_REF_TEMPLATE.format(tm_snippets="\n".join(snippets))


def _build_parallel_strategy_context(strategy_book: StrategyBook) -> str:
    """构建并行模式的策略上下文（每个chunk独立携带·统一指导风格）"""
    direction = strategy_book.direction or "en_to_zh"
    lang_label = "英文 → 中文" if direction == "en_to_zh" else "中文 → 英文"
    return f"""翻译策略（所有chunk统一遵守）：
- ICT子领域：{strategy_book.ict_domain}
- 难度：{strategy_book.difficulty}
- 风格：{strategy_book.style}
- 直译/意译比例：{strategy_book.literal_ratio}
- 翻译方向：{lang_label}
- 目标读者：{strategy_book.target_audience}
- 规则：{json.dumps(strategy_book.rules, ensure_ascii=False)}

跨chunk一致性要求：
1. 术语表中的术语必须使用指定译法（所有chunk统一）
2. 技术缩写首次出现给全称+缩写（不同chunk各自独立）
3. {{NT_n}}占位符原样保留不译
4. 风格保持统一（专业·简洁·主动语态）
"""
