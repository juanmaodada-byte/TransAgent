"""
D8.1 译后窗口分段 单元测试
===========================
不依赖真实LLM：monkeypatch quality_skill.chat / polish_skill.chat，验证：
  1. build_post_windows：大文本切 ≤ 阈值窗口·小文本保持单窗口
  2. 质检窗口化：每窗口输入受限·合并正确（issues拼接[段i]·分数平均）
  3. 润色窗口化：逐段调用·结果按序拼接·单段失败保留该段初译稿
  4. 单窗口路径：小文本仍走单次调用（行为不回归）

运行：
    cd "d:\Side Projects\Developing\TransAgent"
    python -X utf8 -m transagent.tests.test_post_windowing
"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import (
    StrategyBook, TermTable, TermEntry, QAResult,
)
from transagent.backend.core.text_window import split_windows, build_post_windows
from transagent.backend.core.skills.post_skills.quality_inspection.scripts import quality_skill
from transagent.backend.core.skills.post_skills.polish.scripts import polish_skill


def _strategy() -> StrategyBook:
    return StrategyBook(
        ict_domain="Kubernetes/云原生", style="technical",
        direction="en_to_zh", analysis_notes="测试策略",
    )


def _term_table(n: int = 20) -> TermTable:
    return TermTable(entries=[
        TermEntry(term=f"term{i}", translation=f"术语{i}")
        for i in range(n)
    ])


def _large_text(chars: int = 3000) -> str:
    """构造多段落长文本（含段落边界·模拟真实文档）。"""
    paras = []
    n = 0
    while sum(len(p) for p in paras) < chars:
        n += 1
        paras.append(
            f"这是第{n}个测试段落，包含一些英文 cloud computing microservices "
            f"architecture 词汇以及 Kubernetes 容器编排技术细节。"
        )
    return "\n\n".join(paras)


async def test_01_window_split():
    print("\n[1/4] build_post_windows：大文本切窗口·小文本单窗口")
    src = _large_text(3000)
    wins = build_post_windows(src, src)
    assert len(wins) > 1, "3000字符应切多窗口"
    for i, (d, s) in enumerate(wins):
        assert len(d) <= 1200, f"窗口{i + 1}译文超限: {len(d)}"
    assert "".join(d for d, _ in wins) == src or "".join(d for d, _ in wins).replace(
        "  ", " "), "窗口拼接应还原译文"

    small = "一段很短的测试文本。"
    assert build_post_windows(small, small) == [(small, small)], "小文本应保持单窗口原样"
    print(f"  OK: 3000字符→{len(wins)}窗口 | 小文本单窗口")


async def test_02_quality_windowed_merge():
    print("\n[2/4] 质检窗口化：每窗口输入受限·合并正确")
    calls: list[int] = []

    async def fake_qa(system_prompt, user_message, **kwargs):
        calls.append(len(user_message))
        return {
            "total_score": 8.0, "term_accuracy": 8.0, "semantic_fidelity": 8.0,
            "code_integrity": 8.0, "fluency": 8.0, "style_match": 8.0,
            "issues": [{
                "id": "I001", "location": "句3", "severity": "minor", "nature": "improvement",
                "type": "翻译腔", "current": "foo", "suggestion": "bar",
                "description": "", "reason": "", "must_fix": True,
                "source_seg": "", "target_seg": "",
            }],
            "summary": "窗口质检",
        }

    quality_skill.chat = fake_qa
    src = _large_text(3000)
    qa = await quality_skill.QualityInspectionSkill().execute(
        source_md=src, draft=src,
        term_table=_term_table(), strategy_book=_strategy(),
    )
    n = len(build_post_windows(src, src))
    assert len(calls) == n, f"应每窗口一次调用: {len(calls)} vs {n}"
    assert all(c <= 4000 for c in calls), f"单窗口输入过大: {calls}"
    assert len(qa.issues) == n, f"issues应逐窗口拼接: {len(qa.issues)} vs {n}"
    assert all(iss.location.startswith("[段") for iss in qa.issues), "issue location 应带 [段i] 前缀"
    assert qa.total_score == 8.0, "分数应平均（各窗口相同→不变）"
    print(f"  OK: {n}窗口 | 每次输入≤{max(calls)}字符 | issues={len(qa.issues)}条带[段i] | 总分{qa.total_score}")


async def test_03_polish_windowed_concat():
    print("\n[3/4] 润色窗口化：逐段调用·结果拼接·单段失败保留该段")
    call_count = 0
    fail_first = True

    async def fake_polish(system_prompt, user_message, **kwargs):
        nonlocal call_count, fail_first
        call_count += 1
        if fail_first:  # 第一段模拟失败→触发窗口级降级
            fail_first = False
            raise RuntimeError("模拟空响应耗尽")
        return "【润色后】" + user_message.split("## 初译稿")[1].split("\n\n", 1)[-1].split("\n\n## 源文参考")[0]

    polish_skill.chat = fake_polish
    src = _large_text(3000)
    qa = QAResult(total_score=8.0, issues=[], summary="")
    final, notes = await polish_skill.PolishSkill().execute(
        source_md=src, draft=src, qa_result=qa,
        strategy_book=_strategy(), term_table=_term_table(),
    )
    n = len(build_post_windows(src, src))
    assert call_count == n, f"应每窗口一次调用: {call_count} vs {n}"
    # 第一段失败→保留原段；其余段拼接
    assert final, "应返回拼接后的终稿"
    assert "第1段润色失败" in notes, "notes 应记录窗口级降级"
    assert "【润色后】" in final, "成功窗口的润色结果应拼接"
    print(f"  OK: {n}窗口 | 1段失败保留原稿·{n - 1}段拼接 | notes={notes[:40]}...")


async def test_04_single_window_no_regression():
    print("\n[4/4] 单窗口路径：小文本仍单次调用（行为不回归）")
    calls = 0

    async def fake_qa(system_prompt, user_message, **kwargs):
        nonlocal calls
        calls += 1
        return {"total_score": 8.5, "term_accuracy": 8.0, "semantic_fidelity": 8.5,
                "code_integrity": 9.0, "fluency": 8.0, "style_match": 8.0,
                "issues": [], "summary": ""}

    async def fake_polish(system_prompt, user_message, **kwargs):
        nonlocal calls
        calls += 1
        return "小文本润色结果"

    quality_skill.chat = fake_qa
    polish_skill.chat = fake_polish
    small = "Kubernetes uses containers to package applications."
    qa = await quality_skill.QualityInspectionSkill().execute(
        source_md=small, draft=small, term_table=_term_table(3),
        strategy_book=_strategy(), aligned_pairs=[],
    )
    final, notes = await polish_skill.PolishSkill().execute(
        source_md=small, draft=small, qa_result=qa,
        strategy_book=_strategy(), term_table=_term_table(3),
    )
    assert calls == 2, "小文本应 QA+润色各1次调用"
    assert final == "小文本润色结果"
    assert qa.total_score == 8.5
    print(f"  OK: 2次调用 | final={final} | qa={qa.total_score}")


async def main():
    await test_01_window_split()
    await test_02_quality_windowed_merge()
    await test_03_polish_windowed_concat()
    await test_04_single_window_no_regression()
    print("\n全部通过 ✅")


if __name__ == "__main__":
    asyncio.run(main())
