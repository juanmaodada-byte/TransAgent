"""
技能二：术语提取（译前）·实现脚本
===============================
Vibe Coder A | v1.3 | 2026-08-19 (D7·术语提取/翻译拆分)

职责：只提取术语（term + 上下文片段），**不**定译法。
      译法由技能三 TermTranslationSkill 负责（RAG优先·LLM兜底）。

说明：本目录上级的 skill.md（由Skill基类运行时加载·纯提取标准）。
本文件只放实现：组装用户消息 → 调用LLM → 解析候选 → 超长分包。

D7拆分说明：
  - 原 term_skill 一次调用做「提取+译法+来源」复杂JSON → 易触发 json_mode 空响应
  - 现改为纯提取：LLM只输出 {"terms": [{"term", "context"}]} 简单结构，稳定很多
  - RAG查证 / LLM译法 逻辑迁至 term_translation/scripts/term_translation_skill.py
  - 超长 fragment 分包（_split_fragment）保留在本技能

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + config
不依赖：技能一/技能三（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import asyncio
import re

from transagent.interface import StrategyBook, UserPrefs
from transagent.backend.core.llm_client import chat
from transagent.backend.config import get_config
from transagent.backend.core.skills.skill import Skill, register_skill


@register_skill
class TermExtractionSkill(Skill):
    """
    技能二：术语提取。输入 方向+领域标签+文本（全文或某个chunk）→ 输出术语候选列表。

    输出形态：list[dict]，每项 {"term": 术语, "context": 术语所在上下文片段}。
    只提名字不定译法（译法由 TermTranslationSkill 负责）。

    方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en），
    只执行 skill.md 中对应方向的提取标准。
    """
    name = "term_extraction"
    description = "从文本中提取术语（只提名字，不定译法）"
    skill_dir = "pre_skills/term_extraction"
    temperature = 0.2
    max_tokens = 8000  # D9.1：推理型模型思考耗token·过小→空响应
    json_mode = False   # D7: 按行输出（不用json_mode·绕开模型JSON空响应/解析失败）
    requires = {"source_md", "strategy_book", "user_prefs"}  # D6共享池
    provides = {"term_candidates"}                            # D6共享池：产出候选

    async def execute(
        self,
        fragment: str,
        strategy: StrategyBook,
        user_prefs: UserPrefs,
        part_label: str = "全文",
    ) -> list[str]:
        """执行一次术语提取（D7·超长fragment自动分包）。

        只提术语名（不定译法）：返回 list[str]，跨包合并去重。
        译法由 TermTranslationSkill 负责（RAG优先·LLM兜底）。
        """
        parts = _split_fragment(fragment)
        if len(parts) <= 1:
            return await self._extract_one(fragment, strategy, user_prefs, part_label)

        # D7: 并行提取各分包（互相独立）——串行 4 包=4×LLM延迟，并行约 1×
        # 并发上限 3，避免同时打太多请求触发 API 限流
        sem = asyncio.Semaphore(3)

        async def _run(i: int, part: str) -> list[str]:
            sub_label = f"{part_label}·分包{i + 1}/{len(parts)}"
            async with sem:
                return await self._extract_one(part, strategy, user_prefs, sub_label)

        part_results = await asyncio.gather(
            *[_run(i, p) for i, p in enumerate(parts)]
        )

        merged: list[str] = []
        seen: set[str] = set()
        for terms in part_results:
            for term in terms:
                if term and term not in seen:
                    seen.add(term)
                    merged.append(term)
        return merged

    async def _extract_one(
        self,
        fragment: str,
        strategy: StrategyBook,
        user_prefs: UserPrefs,
        part_label: str,
    ) -> list[str]:
        """单包术语提取：一次LLM调用，按行输出术语名（list[str]）。"""
        direction = strategy.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy.ict_domain}
本次处理文本：{part_label}

待提取术语的文本：
{fragment}"""

        try:
            result = await chat(
                self.full_system_prompt(),
                user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=False,   # 按行输出·不用json_mode
            )
            text = str(result) if result else ""
            return _parse_line_terms(text)
        except Exception as e:
            print(f"[TermExtractionSkill] 术语提取失败: {e}")
            return []


def _parse_line_terms(text: str) -> list[str]:
    """解析按行输出的术语：每行一个术语，去列表标记/空白/噪音行，按术语名去重。

    确定性解析（不依赖JSON）——提取环节不再有 json_mode 空响应/解析失败。
    """
    terms: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去常见列表前缀（- * • 1. 1) 1、）
        line = re.sub(r'^(?:[-*•]|\d+[.)、])\s*', '', line).strip()
        if not line:
            continue
        # 跳过标题/说明行（以冒号结尾·如 "Terms:" "以下是提取结果："）
        if line.endswith((":", "：")):
            continue
        # 跳过明显非术语的行（过长散文·含句号）
        if len(line) > 120 or "." in line:
            continue
        if line not in seen:
            seen.add(line)
            terms.append(line)
    return terms


def _split_fragment(fragment: str, max_chars: int | None = None) -> list[str]:
    """超长fragment分包（D7）：按空行分段落，段仍超限则硬切。

    保证每个包 ≤ term_extraction_max_chars，避免单次LLM调用输入过长
    （json_mode 长输入返回空·实测4742字符连续失败、848成功）。

    策略：
      ① 按空行分段落（保留表格块/列表块等自然边界）
      ② 单段仍超限 → 在空格/逗号边界硬切，避免切断单词
    """
    if max_chars is None:
        max_chars = getattr(get_config().pipeline, "term_extraction_max_chars", 1500)
    fragment = (fragment or "").strip()
    if not fragment:
        return []
    if len(fragment) <= max_chars:
        return [fragment]

    # ① 按空行分段落
    paras = [p.strip() for p in fragment.split("\n\n") if p.strip()]
    parts: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            parts.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur:
        parts.append(cur)

    # ② 单段仍超限 → 硬切（优先在分隔符边界）
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        while len(p) > max_chars:
            cut = p[:max_chars]
            boundary = max(cut.rfind(" "), cut.rfind(","),
                           cut.rfind("，"), cut.rfind("。"))
            if boundary < max_chars * 0.5:
                boundary = max_chars
            final.append(p[:boundary].strip())
            p = p[boundary:].strip()
        if p:
            final.append(p)
    return final
