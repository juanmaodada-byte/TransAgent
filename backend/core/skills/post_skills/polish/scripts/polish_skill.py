"""
技能二：润色（译后）·实现脚本
=============================
Vibe Coder A | v1.0 | 2026-08-10 (D5)

说明书：本目录上级的 skill.md（由Skill基类运行时加载·双方向章节合一）。
本文件只放实现：组装用户消息 → 调用LLM → 返回（终稿, 润色说明）。

方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en）。

输入：源文 + 初译稿 + 质检报告 + 策略书
输出：（终稿, 润色说明）——修复质检问题 + 消除翻译腔 + 提升自然度

降级约定：润色失败时技能内部降级（交付初译稿）——"润色失败交付初译稿"是本技能职责内兜底。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat
不依赖：技能一、译后agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import asyncio

from transagent.interface import (
    StrategyBook, TermTable, QAResult,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill
from transagent.backend.core.text_window import build_post_windows  # D8.1：窗口分段防空响应


@register_skill
class PolishSkill(Skill):
    """技能二：润色。输入 源文+初译稿+质检报告+策略书 → 输出（终稿, 润色说明）。"""
    name = "polish"
    description = "ICT领域受约束译后编辑：在源文、翻译策略、项目术语表和QA报告约束下，以最小必要修改修复初译稿并输出终稿"
    skill_dir = "post_skills/polish"
    temperature = 0.4
    max_tokens = 8000  # D9.1：推理型模型思考会耗光token预算→finish=length且content为空；8000才有正文余量
    json_mode = False
    requires = {"source_md", "draft", "qa_report", "strategy_book", "term_table"}  # D6共享池
    provides = {"final_text", "polish_notes"}      # D6共享池：产出终稿 + 润色说明

    async def execute(
        self,
        source_md: str,
        draft: str,
        qa_result: QAResult,
        strategy_book: StrategyBook,
        term_table: TermTable,
    ) -> tuple[str, str]:
        """执行一次润色（含校对：修复+润色一次完成）。

        Args:
            source_md: 源文（前2000字·仅作语义核对）
            draft: 初译稿
            qa_result: 质检报告（问题必须逐条修复·含建议/是否必须修复）
            strategy_book: 策略书（direction 字段路由方向 + 策略摘要作为约束）
            term_table: 项目术语表（translate必须指定译法·notranslate必须保留原文）

        Returns:
            (终稿, 润色说明)
        """
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"

        strategy_text = "\n".join([
            f"- ICT子领域: {strategy_book.ict_domain}",
            f"- 目标风格: {strategy_book.style}",
            f"- 目标受众: {strategy_book.target_audience}",
            f"- 难度: {strategy_book.difficulty}",
            f"- 直译/意译比例: {strategy_book.literal_ratio}",
            f"- 规则: {strategy_book.rules}",
        ] + ([f"- 策略判断依据: {strategy_book.analysis_notes}"]
             if strategy_book.analysis_notes else []))

        qa_scores = (
            f"- 术语准确性: {qa_result.term_accuracy}\n"
            f"- 语义忠实度: {qa_result.semantic_fidelity}\n"
            f"- 代码完整性: {qa_result.code_integrity}\n"
            f"- 流畅性: {qa_result.fluency}\n"
            f"- 风格匹配: {qa_result.style_match}"
        )

        # D8.1：按字符窗口分段润色（长文档不再整包塞给单次调用→空响应风暴）
        windows = build_post_windows(draft, source_md)
        if len(windows) <= 1:
            term_context = _term_context(term_table, limit=15)
            issues_text = _build_issues_text(qa_result, max_issues=None)
            user_message = f"""翻译方向：{direction_label}

## 翻译策略
{strategy_text}

## 项目术语表
{term_context}

## 质检报告（总分{qa_result.total_score}）
{qa_scores}

## 问题清单（必须逐条修复）
{issues_text}

## 初译稿
{draft}

## 源文参考（前2000字·仅作语义核对）
{source_md[:2000]}"""

            try:
                final_text = await chat(
                    self.full_system_prompt(), user_message,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                final = final_text if isinstance(final_text, str) else str(final_text)
                return final, _build_polish_notes(qa_result, direction)
            except Exception as e:
                print(f"[PolishSkill] 润色失败，返回初译稿: {e}")
                fallback = "润色失败·交付初译稿" if direction != "zh_to_en" else "Polish failed; delivering draft"
                return draft, f"{fallback}: {e}"

        # ── 多窗口：逐段润色 → 按序拼接（窗口级降级：单段失败保留该段初译稿） ──
        term_context = _term_context(term_table, limit=10)
        issues_text = _build_issues_text(qa_result, max_issues=12)
        polished: list[str] = []
        window_notes: list[str] = []
        total = len(windows)

        # D8.1：窗口并行润色（复用术语提取的并行模式）——避免空响应重试风暴下串行累计超时
        sem = asyncio.Semaphore(3)

        def _build_msg(i: int, dwin: str, swin: str) -> str:
            return f"""翻译方向：{direction_label}

## 翻译策略
{strategy_text}

## 项目术语表
{term_context}

## 质检报告（总分{qa_result.total_score}）
{qa_scores}

## 问题清单（必须逐条修复）
{issues_text}

## 初译稿（第{i + 1}/{total}部分）
{dwin}

## 源文参考（对应部分·仅作语义核对）
{swin}"""

        async def _run(i: int, win: tuple) -> tuple[int, str, str | None]:
            dwin, swin = win
            async with sem:
                try:
                    seg = await chat(
                        self.full_system_prompt(), _build_msg(i, dwin, swin),
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                    return i, seg if isinstance(seg, str) else str(seg), None
                except Exception as e:
                    print(f"[PolishSkill] 窗口{i + 1}润色失败，保留该段初译稿: {e}")
                    return i, dwin, f"第{i + 1}段润色失败"

        results = await asyncio.gather(*[_run(i, w) for i, w in enumerate(windows)])
        results.sort(key=lambda r: r[0])
        for _, seg, err in results:
            polished.append(seg)
            if err:
                window_notes.append(err)

        final = "\n\n".join(p.strip() for p in polished if p) or draft
        notes = _build_polish_notes(qa_result, direction)
        if window_notes:
            notes += f"（{'；'.join(window_notes)}）"
        return final, notes


def _term_context(term_table: TermTable, limit: int = 15) -> str:
    """术语表上下文（D8.1：窗口化时收窄到 limit 条，控制单调用输入）。"""
    if not term_table.entries:
        return "（无术语表）"
    lines = [
        f"- {e.term} → {e.translation}" + ("【不译】" if e.action == "notranslate" else "")
        for e in term_table.entries[:limit]
    ]
    return "\n".join(lines)


def _build_issues_text(qa_result: QAResult, max_issues: int | None = None) -> str:
    """把质检报告的每条 Issue 组装为完整文本（含 id/nature/current/suggestion/must_fix）。

    D6：若 Issue 带结构化定位（source_seg/target_seg·来自句对匹配），
    追加源句/译句原文，供润色 LLM 精准定位、最小范围修改。
    D8.1：max_issues 限制条数（窗口化时避免问题清单撑爆单调用输入）。
    """
    if not qa_result.issues:
        return "（无具体问题）"
    issues = qa_result.issues if max_issues is None else qa_result.issues[:max_issues]
    lines = []
    for i in issues:
        seg = f"- [{i.severity}] {i.location}: {i.type}"
        if i.nature:
            seg += f"（{i.nature}）"
        if i.source_seg:
            seg += f" | 源句: {i.source_seg}"
        if i.target_seg:
            seg += f" | 译句: {i.target_seg}"
        if i.current:
            seg += f" | 当前: {i.current}"
        if i.suggestion:
            seg += f" → 建议: {i.suggestion}"
        if i.description:
            seg += f" | 说明: {i.description}"
        if i.reason:
            seg += f" | 原因: {i.reason}"
        seg += " | 必须修复" if i.must_fix else " | 非强制"
        lines.append(seg)
    if max_issues is not None and len(qa_result.issues) > max_issues:
        lines.append(f"（其余 {len(qa_result.issues) - max_issues} 条问题省略·按已列出的修复）")
    return "\n".join(lines)


def _build_polish_notes(qa_result: QAResult, direction: str) -> str:
    """按质检结果生成润色说明（方向感知）。"""
    en = direction == "zh_to_en"
    if qa_result.total_score >= 9.5:
        return "Draft quality excellent, minimal touch-up" if en else "初译质量极高，仅做微调"
    if not qa_result.issues:
        return "Reduced awkward phrasing, improved fluency" if en else "消除翻译腔·提升自然度"
    return (
        f"Fixed {len(qa_result.issues)} issues, improved fluency"
        if en else
        f"修复{len(qa_result.issues)}个问题·消除翻译腔·提升自然度"
    )
