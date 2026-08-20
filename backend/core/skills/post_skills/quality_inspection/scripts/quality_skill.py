"""
技能一：质检（译后）·实现脚本
=============================
Vibe Coder A | v1.1 | 2026-08-15 (D6)

说明书：本目录上级的 skill.md（由Skill基类运行时加载·双方向章节合一）。
本文件只放实现：组装用户消息 → 调用LLM → 解析为QAResult。

方向路由：以策略书 direction 字段为准（en_to_zh / zh_to_en）。

输入：源文 + 初译稿 + 术语表 + 策略书
      （D6增强·可选）句对齐结果 aligned_pairs / 用户偏好 user_prefs / 占位符表 placeholder_map
输出：QAResult（5维评分 + 问题列表 + 总结）

D6更新（共享池·结构化定位）：
  - 从共享池新增读取：aligned_pairs（初译稿对齐）、user_prefs、placeholder_map（均可选·缺省降级）
  - 有句对时：prompt 按「句对编号 源|译」展示，LLM 摘抄有问题的源句/译句原文
  - 定位以系统为准：locate_quote() 把 LLM 摘抄句模糊匹配到具体句对，
    权威写入 QAIssue 的 chunk_id / pair_index / source_seg / target_seg（LLM只负责摘抄指认）
  - 无句对时降级：回退到 原文+译文 分段展示，location 保留 LLM 自由文本

降级约定：质检失败时技能内部降级（基础评分8.0）——"质检降级用基础评分"是本技能职责内兜底。

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat + pipeline/aligner.locate_quote
不依赖：技能二、译后agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

import asyncio
import re

from transagent.interface import (
    TermTable, StrategyBook, QAResult, QAIssue,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill
from transagent.backend.core.text_window import build_post_windows  # D8.1：窗口分段防空响应
from transagent.backend.pipeline.aligner import locate_quote


def _as_bool(value) -> bool:
    """容错布尔解析：LLM JSON 可能返回 true/false 或字符串 'true'/'false'。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是")


def _as_int(value, default: int = -1) -> int:
    """容错整数解析：LLM JSON 可能返回数字或字符串数字。"""
    if isinstance(value, bool):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


@register_skill
class QualityInspectionSkill(Skill):
    """技能一：质检。输入 源文+初译稿+术语表+策略书（可选句对/偏好/占位符）→ 输出质检报告。"""
    name = "quality_inspection"
    description = "ICT翻译质量质检：基于源文、译前策略与项目术语约束，识别术语、语义、代码/参数、语言与风格问题，并输出可执行的结构化QA报告供译后编辑Skill修复"
    skill_dir = "post_skills/quality_inspection"
    temperature = 0.3
    max_tokens = 8000  # D9.1：推理型模型会先烧思考token，max_tokens过小→finish=length且content为空
    json_mode = True
    requires = {"source_md", "draft", "term_table", "strategy_book"}  # D6共享池：核心4件
    provides = {"qa_report"}                      # D6共享池：产出质检报告

    async def execute(
        self,
        source_md: str,
        draft: str,
        term_table: TermTable,
        strategy_book: StrategyBook,
        aligned_pairs=None,       # D6：初译稿对齐句对（可选·缺省降级为分段展示）
        user_prefs=None,          # D6：用户偏好（可选·风格检查参考）
        placeholder_map=None,     # D6：占位符表（可选·代码完整性核对）
    ) -> QAResult:
        """执行一次质检。

        Args:
            source_md: 源文（受保护MD·含占位符）
            draft: 初译稿
            strategy_book: 策略书（读取其 direction 字段路由质检标准）
            aligned_pairs: 初译稿对齐结果（list[AlignedPair]·来自共享池）——有则按句对定位
            user_prefs: 用户偏好（default_style 作风格检查参考）
            placeholder_map: 占位符表（nt_count/t_count 作代码完整性核对参考）
        """
        direction = strategy_book.direction or "en_to_zh"   # 以策略书direction字段路由
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"

        # D8.1：长文档按字符窗口分段质检（整稿一次性质检→长输入空响应风暴）
        windows = build_post_windows(draft, source_md)
        if len(windows) <= 1:
            return await self._execute_single(
                source_md, draft, term_table, strategy_book, aligned_pairs,
                user_prefs, placeholder_map, direction_label,
            )
        return await self._execute_windows(
            windows, term_table, strategy_book, direction_label,
            user_prefs, placeholder_map,
        )

    # ── 单窗口：现有路径（保留句对结构化定位） ────────────────────────────
    async def _execute_single(
        self, source_md, draft, term_table, strategy_book, aligned_pairs,
        user_prefs, placeholder_map, direction_label,
    ) -> QAResult:
        term_context = _term_context(term_table, limit=15)
        style_ctx = _build_style_context(user_prefs, strategy_book.style)
        placeholder_ctx = _build_placeholder_context(placeholder_map)
        pair_view = _build_pair_view(aligned_pairs) if aligned_pairs else ""

        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy_book.ict_domain}
目标风格：{strategy_book.style}
{style_ctx}
{placeholder_ctx}

## 项目术语表
{term_context}
"""
        if pair_view:
            # D6：按句对展示（源↔译对齐）·定位必须给出句对编号 + 摘抄原句
            user_message += f"\n## 句对齐（源↔译·定位问题请给出句对编号并逐字摘抄源/译句）\n{pair_view}"
        else:
            # 降级：无对齐结果 → 原文+译文分段展示（location 保留自由文本）
            user_message += f"\n## 源文（前3000字）\n{source_md[:3000]}\n\n## 译文（前4000字）\n{draft[:4000]}"

        qa = await self._call_qa(user_message, aligned_pairs or [])
        return qa if qa is not None else QAResult(
            total_score=8.0, summary="质检降级·使用基础评分")

    # ── 多窗口：逐窗口质检 → 合并（issues 拼接·分数平均·summary 拼接） ──────
    async def _execute_windows(
        self, windows, term_table, strategy_book, direction_label,
        user_prefs, placeholder_map,
    ) -> QAResult:
        term_context = _term_context(term_table, limit=10)
        style_ctx = _build_style_context(user_prefs, strategy_book.style)
        placeholder_ctx = _build_placeholder_context(placeholder_map)
        total = len(windows)

        score_keys = ("total_score", "term_accuracy", "semantic_fidelity",
                      "code_integrity", "fluency", "style_match")
        sums = {k: 0.0 for k in score_keys}
        all_issues: list = []
        summaries: list[str] = []
        ok_count = 0

        # D8.1：窗口并行质检（复用术语提取的并行模式）——N 窗口从 N×耗时 变 ≈1×耗时，
        # 避免 deepseek 空响应重试风暴下串行累计超时
        sem = asyncio.Semaphore(3)

        def _build_msg(i: int, dwin: str, swin: str) -> str:
            return f"""翻译方向：{direction_label}
ICT子领域：{strategy_book.ict_domain}
目标风格：{strategy_book.style}
{style_ctx}
{placeholder_ctx}

## 项目术语表
{term_context}

## 源文（第{i + 1}/{total}部分）
{swin}

## 译文（第{i + 1}/{total}部分）
{dwin}"""

        async def _run(i: int, win: tuple) -> tuple[int, QAResult | None]:
            dwin, swin = win
            async with sem:
                # 窗口化不传 aligned_pairs（避免整稿句对视图撑爆输入）；location 由 LLM 摘抄自述
                return i, await self._call_qa(_build_msg(i, dwin, swin), [])

        results = await asyncio.gather(*[_run(i, w) for i, w in enumerate(windows)])
        for i, qa in results:
            if qa is None:
                print(f"[QualityInspectionSkill] 窗口{i + 1}质检失败→该段基础评分")
                continue
            ok_count += 1
            for k in score_keys:
                sums[k] += getattr(qa, k)
            for iss in qa.issues:
                iss.location = f"[段{i + 1}] {iss.location}"
                all_issues.append(iss)
            if qa.summary:
                summaries.append(f"[段{i + 1}] {qa.summary}")

        if ok_count == 0:
            print("[QualityInspectionSkill] 全部窗口质检失败→基础评分")
            return QAResult(total_score=8.0, summary="质检降级·使用基础评分")

        return QAResult(
            total_score=round(sums["total_score"] / ok_count, 1),
            term_accuracy=round(sums["term_accuracy"] / ok_count, 1),
            semantic_fidelity=round(sums["semantic_fidelity"] / ok_count, 1),
            code_integrity=round(sums["code_integrity"] / ok_count, 1),
            fluency=round(sums["fluency"] / ok_count, 1),
            style_match=round(sums["style_match"] / ok_count, 1),
            issues=all_issues,
            summary="\n".join(summaries),
        )

    # ── 单次质检调用：chat(json_mode)→解析；失败返回 None（由调用方降级） ──
    async def _call_qa(self, user_message: str, aligned_pairs: list) -> QAResult | None:
        try:
            result = await chat(
                self.full_system_prompt(), user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=self.json_mode,
            )
            if not isinstance(result, dict):
                return None
            issues = [
                _parse_issue(iss, aligned_pairs)
                for iss in result.get("issues", [])
            ]
            return QAResult(
                total_score=float(result.get("total_score", 8.0)),
                term_accuracy=float(result.get("term_accuracy", 8.0)),
                semantic_fidelity=float(result.get("semantic_fidelity", 8.0)),
                code_integrity=float(result.get("code_integrity", 8.0)),
                fluency=float(result.get("fluency", 8.0)),
                style_match=float(result.get("style_match", 8.0)),
                issues=issues,
                summary=result.get("summary", ""),
            )
        except Exception as e:
            print(f"[QualityInspectionSkill] 质检调用失败: {e}")
            return None


# ══════════════════════════════════════════════════════════════════
# 解析：LLM摘抄指认 → 系统权威定位
# ══════════════════════════════════════════════════════════════════

def _term_context(term_table: TermTable, limit: int = 15) -> str:
    """术语表上下文（D8.1：窗口化时收窄到 limit 条，控制单调用输入）。"""
    if not term_table.entries:
        return "（无术语表）"
    lines = [
        f"- {e.term} → {e.translation}" + ("【不译】" if e.action == "notranslate" else "")
        for e in term_table.entries[:limit]
    ]
    return "\n".join(lines)


def _parse_issue(iss: dict, aligned_pairs: list) -> QAIssue:
    """LLM返回的issue dict → QAIssue；再用句对匹配做系统权威定位。"""
    issue = QAIssue(
        id=str(iss.get("id", "")),
        location=iss.get("location", ""),
        severity=iss.get("severity", "minor"),
        nature=iss.get("nature", ""),
        type=iss.get("type", ""),
        current=iss.get("current", ""),
        suggestion=iss.get("suggestion", ""),
        description=iss.get("description", ""),
        reason=iss.get("reason", ""),
        must_fix=_as_bool(iss.get("must_fix", False)),
        chunk_id=str(iss.get("chunk_id", "")),
        pair_index=_as_int(iss.get("pair_index")),
        source_seg=str(iss.get("source_seg", "")),
        target_seg=str(iss.get("target_seg", "")),
    )
    # D6防御：LLM偶发把 current 原样填进 suggestion（no-op 建议·如本次"更小的服务"重复）
    # → 清空 suggestion，避免润色拿到无意义建议（真实建议通常写在 description）。
    if _same_text(issue.suggestion, issue.current):
        if issue.suggestion:
            print(f"[QualityInspectionSkill] 建议与当前相同·忽略该建议: {issue.suggestion[:40]}")
        issue.suggestion = ""
    return _resolve_issue_location(issue, aligned_pairs)


def _norm_issue_text(s: str) -> str:
    """归一化：去空白+常见标点+小写，用于判断两段文字是否实质相同。"""
    return re.sub(r"[\s，。；！？,.!?;:'\"'\"·—、…]", "", s).lower()


def _same_text(a: str, b: str) -> bool:
    """容错比较：归一化后相同 → 视为同一段文字（如 suggestion 重复 current）。"""
    if not a or not b:
        return False
    na = _norm_issue_text(a)
    nb = _norm_issue_text(b)
    return bool(na) and na == nb


def _resolve_issue_location(issue: QAIssue, aligned_pairs: list) -> QAIssue:
    """系统权威定位：把 LLM 摘抄的源/译句匹配到句对，覆盖其填写的编号。"""
    if not aligned_pairs:
        return issue
    pair, idx = locate_quote(aligned_pairs, issue.source_seg, issue.target_seg)
    if pair is None:
        return issue   # 未匹配 → 保留LLM指认（可能不精确·不误导）
    issue.chunk_id = pair.chunk_id or issue.chunk_id
    issue.pair_index = idx
    issue.source_seg = pair.source_seg       # 权威句对源句
    issue.target_seg = pair.target_seg       # 权威句对译句
    issue.location = _format_location(issue)
    return issue


def _format_location(issue: QAIssue) -> str:
    """给人看的定位字符串：'chunk_001 · 句对7'。"""
    if issue.pair_index >= 0:
        base = f"句对{issue.pair_index + 1}"
        return f"{issue.chunk_id} · {base}" if issue.chunk_id else base
    return issue.location or "未定位"


# ══════════════════════════════════════════════════════════════════
# prompt 片段构建（D6增强）
# ══════════════════════════════════════════════════════════════════

def _build_style_context(user_prefs, strategy_style: str) -> str:
    """用户偏好风格：与策略书风格并列展示（策略书为准·偏好作参考）。"""
    if user_prefs is None:
        return ""
    pref_style = getattr(user_prefs, "default_style", "") or ""
    if not pref_style:
        return ""
    if pref_style == strategy_style:
        return f"用户偏好风格：{pref_style}"
    return f"用户偏好风格：{pref_style}（与策略风格{strategy_style}不同·以策略书为准）"


def _build_placeholder_context(placeholder_map) -> str:
    """占位符统计：提醒核对 {NT_n}/{T_n} 是否原样保留。"""
    if placeholder_map is None:
        return ""
    nt = getattr(placeholder_map, "nt_count", 0)
    t = getattr(placeholder_map, "t_count", 0)
    return f"占位符：共{nt + t}处（{nt}不可译NT + {t}可译T）· 核对译文是否原样保留"


def _build_pair_view(pairs: list, max_pairs: int = 60, max_chars: int = 5000) -> str:
    """把对齐句对拼成编号视图：'句对1 | 源: ... | 译: ...'（截断控制）。"""
    lines: list[str] = []
    used = 0
    for i, p in enumerate(pairs):
        src = (p.source_seg or "").strip().replace("\n", " ")[:200]
        tgt = (p.target_seg or "").strip().replace("\n", " ")[:200]
        line = f"句对{i + 1} | 源: {src} | 译: {tgt}"
        used += len(line)
        if len(lines) >= max_pairs or used > max_chars:
            break
        lines.append(line)
    if len(pairs) > len(lines):
        lines.append(f"（已省略后续 {len(pairs) - len(lines)} 个句对·超出检查范围）")
    return "\n".join(lines)
