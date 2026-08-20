"""
D6 共享池 + 结构化定位 单元测试
================================
Vibe Coder A | v1.0 | 2026-08-15 (D6)

不依赖真实LLM：monkeypatch 各技能模块的 `chat`，验证：
  1. SharedPool 可用性校验：require 齐/缺
  2. 审计登记：providers / consumers
  3. align_chunks：按chunk逐块对齐·填充 chunk_id
  4. locate_quote：LLM摘抄句 → 系统权威匹配句对（含"问题译句"也能匹配）
  5. 质检技能：读句对/偏好/占位符 → 输出结构化定位（系统覆盖LLM编号）
  6. 译后agent池子路径：读池子 → 写回 qa_report/final_text/polish_notes
  7. 译前agent池子路径：写回 strategy_book/term_table
  8. 译中agent池子路径：写回 chunk_drafts/draft/consistency_report

运行：
    cd "d:\Side Projects\Developing\TransAgent"
    python -X utf8 -m transagent.tests.test_shared_pool_d6
"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import (
    Chunk, PlaceholderMap, UserPrefs, StrategyBook, TermTable, TermEntry,
)
from transagent.backend.core.shared_pool import SharedPool
from transagent.backend.pipeline.aligner import align_chunks, locate_quote
from transagent.backend.core.skills.post_skills.quality_inspection.scripts import quality_skill
from transagent.backend.core.skills.post_skills.polish.scripts import polish_skill
from transagent.backend.core.skills.pre_skills.strategy_formulation.scripts import strategy_skill
from transagent.backend.core.skills.pre_skills.term_extraction.scripts import term_skill
from transagent.backend.core.skills.translate_skills.chunk_translate.scripts import chunk_translate_skill
from transagent.backend.core.skills.translate_skills.consistency_fix.scripts import consistency_skill
from transagent.backend.core.pre_agent import spawn_pre_translate
from transagent.backend.core.translate_agent import spawn_translate
from transagent.backend.core.post_agent import spawn_post_translate

# ── RAG 关闭：本套件非 RAG 专项，避免 bge-m3 加载/网络 ──
from transagent.backend.knowledge import rag_terms as _rag_mod


def _no_rag(*a, **kw):
    return []


_rag_mod.search_rag = _no_rag

# ── 测试数据 ────────────────────────────────────────────────────────

CHUNKS = [
    Chunk(
        chunk_id="chunk_1",
        source_text="## Heading\n\nKubernetes uses containers to package applications. "
                    "A rolling update allows zero downtime.",
        token_estimate=40,
        heading_path=["## Heading"],
        order=1,
    ),
]
CHUNK_DRAFTS = ["## 标题\n\nKubernetes 使用容器来打包应用。滚动更新允许零停机。"]

SOURCE_MD = CHUNKS[0].source_text
DRAFT = CHUNK_DRAFTS[0]

STRATEGY = StrategyBook(
    ict_domain="Kubernetes/云原生", style="technical",
    difficulty="medium", direction="en_to_zh",
)
TERM_TABLE = TermTable(entries=[TermEntry(term="rolling update", translation="滚动更新")])

PAIRS = align_chunks(CHUNKS, CHUNK_DRAFTS)


def build_pool() -> SharedPool:
    pool = SharedPool(session_id="d6_test")
    pool.source_md = SOURCE_MD
    pool.chunks = CHUNKS
    pool.placeholder_map = PlaceholderMap(nt_count=1, t_count=1)
    pool.user_prefs = UserPrefs(user_id="u1", default_style="technical")
    pool.strategy_book = STRATEGY
    pool.term_table = TERM_TABLE
    pool.tm_refs = []
    pool.chunk_drafts = list(CHUNK_DRAFTS)
    pool.draft = DRAFT
    pool.aligned_pairs = PAIRS
    return pool


# ══════════════════════════════════════════════════════════════════

def test_01_pool_require():
    print("\n[1/8] SharedPool 可用性校验：require 齐/缺")
    pool = build_pool()
    pool.require("source_md", "draft", "aligned_pairs")   # 齐 → 不抛
    pool2 = SharedPool()
    try:
        pool2.require("source_md", "draft")
        raise AssertionError("缺数据应抛错")
    except RuntimeError as e:
        assert "缺数据" in str(e) and "source_md" in str(e), e
    print("  OK: 齐全放行·缺失拦截")


def test_02_pool_audit():
    print("\n[2/8] 审计登记：providers / consumers")
    pool = SharedPool()
    pool.mark_provided({"draft"}, agent="translate")
    pool.mark_provided({"qa_report"}, agent="post")
    pool.mark_consumed({"draft"}, agent="post")
    assert pool.providers["draft"] == {"translate"}
    assert pool.providers["qa_report"] == {"post"}
    assert pool.consumers["draft"] == {"post"}
    print(f"  OK: providers={pool.providers} consumers={pool.consumers}")


def test_03_align_chunks():
    print("\n[3/8] align_chunks：按chunk逐块对齐·填充 chunk_id")
    assert len(PAIRS) >= 2, f"应至少对齐出句对，实际={len(PAIRS)}"
    assert all(p.chunk_id == "chunk_1" for p in PAIRS), [p.chunk_id for p in PAIRS]
    # 第0个应是标题对
    assert "Heading" in PAIRS[0].source_seg or "标题" in PAIRS[0].target_seg
    print(f"  OK: {len(PAIRS)}个句对·chunk_id 均=chunk_1 | 首对: {PAIRS[0].source_seg[:20]} → {PAIRS[0].target_seg[:20]}")


def test_04_locate_quote():
    print("\n[4/8] locate_quote：LLM摘抄句 → 系统权威匹配句对")
    # 正常摘抄（与句对一致）
    pair, idx = locate_quote(PAIRS, "Kubernetes uses containers to package applications.",
                             "Kubernetes 使用容器来打包应用。")
    assert pair is not None and idx >= 0, (pair, idx)
    assert "container" in pair.source_seg
    # 问题译句（译法被改错·如"集装箱"）：仍应匹配到该句对（相似度高）
    bad_pair, bad_idx = locate_quote(PAIRS, "Kubernetes uses containers to package applications.",
                                     "Kubernetes 使用集装箱来打包应用。")
    assert bad_pair is not None and bad_idx == idx, (bad_pair, bad_idx, idx)
    # 无关句子 → 未匹配
    none_pair, none_idx = locate_quote(PAIRS, "The weather is nice today.", "今天天气不错。")
    assert none_pair is None and none_idx == -1, (none_pair, none_idx)
    print(f"  OK: 正常匹配句对{idx + 1} · 问题译句仍匹配句对{bad_idx + 1} · 无关句不匹配")


async def test_05_quality_location_resolution():
    print("\n[5/8] 质检技能：结构化定位（系统覆盖LLM编号）")
    async def fake_qa(system_prompt, user_message, **kwargs):
        # 断言：句对视图已按「句对N | 源|译」展示
        assert "句对1 |" in user_message, "应提供句对齐视图"
        # 断言：用户偏好/占位符上下文已注入
        assert "用户偏好风格" in user_message
        assert "占位符" in user_message
        return {
            "total_score": 8.5, "term_accuracy": 8.0, "semantic_fidelity": 8.5,
            "code_integrity": 9.0, "fluency": 8.0, "style_match": 8.0,
            "issues": [{
                "id": "I001", "location": "", "severity": "minor", "nature": "improvement",
                "type": "翻译腔", "current": "", "suggestion": "", "description": "",
                "reason": "", "must_fix": True,
                # LLM 摘抄指认（译句为错误译法·系统应匹配到权威句对）
                "source_seg": "Kubernetes uses containers to package applications.",
                "target_seg": "Kubernetes 使用集装箱来打包应用。",
            }],
            "summary": "test",
        }

    quality_skill.chat = fake_qa
    pool = build_pool()
    qa = await quality_skill.QualityInspectionSkill().execute(
        source_md=pool.source_md, draft=pool.draft,
        term_table=pool.term_table, strategy_book=pool.strategy_book,
        aligned_pairs=pool.aligned_pairs,
        user_prefs=pool.user_prefs,
        placeholder_map=pool.placeholder_map,
    )
    issue = qa.issues[0]
    assert issue.chunk_id == "chunk_1", issue.chunk_id
    assert issue.pair_index >= 0, issue.pair_index
    assert "container" in issue.source_seg, issue.source_seg      # 权威源句（非LLM摘抄被改写）
    assert "容器" in issue.target_seg, issue.target_seg            # 权威译句（容器·非错误"集装箱"）
    assert "句对" in issue.location, issue.location
    print(f"  OK: chunk_id={issue.chunk_id} | pair_index={issue.pair_index + 1} | "
          f"location={issue.location}")


async def test_06_post_pool_flow():
    print("\n[6/8] 译后agent池子路径：读池子 → 写回 qa_report/final_text/polish_notes")
    async def fake_qa(system_prompt, user_message, **kwargs):
        return {
            "total_score": 8.5, "term_accuracy": 8.0, "semantic_fidelity": 8.5,
            "code_integrity": 9.0, "fluency": 8.0, "style_match": 8.0,
            "issues": [{
                "id": "I001", "location": "", "severity": "minor", "nature": "improvement",
                "type": "翻译腔", "current": "", "suggestion": "", "description": "",
                "reason": "", "must_fix": True,
                "source_seg": "Kubernetes uses containers to package applications.",
                "target_seg": "Kubernetes 使用集装箱来打包应用。",
            }],
            "summary": "test",
        }

    async def fake_polish(system_prompt, user_message, **kwargs):
        return "## 标题\n\nKubernetes 使用容器来打包应用。滚动更新允许零停机。"

    quality_skill.chat = fake_qa
    polish_skill.chat = fake_polish
    pool = build_pool()
    result = await spawn_post_translate(pool)
    assert result.qa_report is not None and result.qa_report.total_score == 8.5
    assert result.final_text and "滚动更新" in result.final_text
    assert result.polish_notes
    # 池子写回
    assert pool.qa_report is result.qa_report
    assert pool.final_text == result.final_text
    assert pool.polish_notes == result.polish_notes
    # 审计 + 质检问题结构化定位已解析
    assert "qa_report" in pool.providers and "final_text" in pool.providers
    issue = result.qa_report.issues[0]
    assert issue.chunk_id == "chunk_1" and issue.pair_index >= 0
    print(f"  OK: qa={result.qa_report.total_score} | final_text={len(result.final_text)}字符 | "
          f"issue定位={issue.location} | providers={sorted(pool.providers)}")


async def test_07_pre_pool_flow():
    print("\n[7/8] 译前agent池子路径：写回 strategy_book/term_table")
    async def fake_pre(system_prompt, user_message, **kwargs):
        if "术语专家" in system_prompt:
            return {"term_table": [{"term": "rolling update", "translation": "滚动更新", "source": "LLM生成"}],
                    "pending_terms": []}
        return {"ict_domain": "Kubernetes/云原生", "domain_confidence": "high",
                "difficulty": "medium", "style": "technical", "literal_ratio": 0.6,
                "target_audience": "开发者", "rules": {"code": "notranslate"},
                "analysis_notes": "K8s指南"}

    strategy_skill.chat = term_skill.chat = fake_pre
    pool = build_pool()
    pool.strategy_book = None          # 重置：由译前写回
    pool.term_table = None
    result = await spawn_pre_translate(pool)
    assert pool.strategy_book is not None and pool.strategy_book.ict_domain == "Kubernetes/云原生"
    assert pool.term_table is not None and pool.term_table.total_count == 1
    assert result.strategy_book is pool.strategy_book
    assert result.term_table is pool.term_table
    assert "strategy_book" in pool.providers and "term_table" in pool.providers
    print(f"  OK: strategy_book.domain={pool.strategy_book.ict_domain} | "
          f"term_table={pool.term_table.total_count}条 | providers={sorted(pool.providers)}")


async def test_08_translate_pool_flow():
    print("\n[8/8] 译中agent池子路径：写回 chunk_drafts/draft/consistency_report")
    async def fake_chunk(system_prompt, user_message, **kwargs):
        return "## 标题\n\nKubernetes 使用容器来打包应用。滚动更新允许零停机。"

    async def fake_consistency(system_prompt, user_message, **kwargs):
        return "\n\n".join(CHUNK_DRAFTS)

    chunk_translate_skill.chat = fake_chunk
    consistency_skill.chat = fake_consistency
    pool = build_pool()
    pool.chunk_drafts = []             # 重置：由译中写回
    pool.draft = ""
    pool.consistency_report = None
    result = await spawn_translate(pool)
    assert pool.chunk_drafts == CHUNK_DRAFTS, pool.chunk_drafts
    assert pool.draft == DRAFT
    assert pool.consistency_report is not None
    assert "chunk_drafts" in pool.providers and "draft" in pool.providers
    print(f"  OK: chunk_drafts={len(pool.chunk_drafts)}块 | draft={len(pool.draft)}字符 | "
          f"providers={sorted(pool.providers)}")


async def test_09_noop_suggestion_guard():
    print("\n[9/9] 质检防御：suggestion 与 current 相同 → 判定无效 no-op 并清空")
    async def fake_qa(system_prompt, user_message, **kwargs):
        return {
            "total_score": 9.0, "term_accuracy": 9.0, "semantic_fidelity": 9.0,
            "code_integrity": 10.0, "fluency": 9.0, "style_match": 9.0,
            "issues": [{
                "id": "I001", "location": "", "severity": "minor", "nature": "improvement",
                "type": "翻译腔",
                "current": "将单体应用拆分为更小的服务",
                "suggestion": "将单体应用拆分为更小的服务",   # LLM 把 current 原样填进 suggestion
                "description": "可优化为'更细粒度的服务'更符合技术表达",
                "reason": "", "must_fix": False,
            }],
            "summary": "test",
        }
    quality_skill.chat = fake_qa
    qa = await quality_skill.QualityInspectionSkill().execute(
        source_md=SOURCE_MD, draft=DRAFT,
        term_table=TERM_TABLE, strategy_book=STRATEGY,
    )
    issue = qa.issues[0]
    assert issue.suggestion == "", f"no-op suggestion 应被清空，实际={issue.suggestion!r}"
    assert issue.current == "将单体应用拆分为更小的服务"
    print("  OK: suggestion 被清空（current 保留·description 承载真实建议）")


async def main():
    print("=" * 60)
    print("  D6 共享池 + 结构化定位 测试（mock LLM）")
    print("=" * 60)
    test_01_pool_require()
    test_02_pool_audit()
    test_03_align_chunks()
    test_04_locate_quote()
    await test_05_quality_location_resolution()
    await test_06_post_pool_flow()
    await test_07_pre_pool_flow()
    await test_08_translate_pool_flow()
    await test_09_noop_suggestion_guard()
    print("\n" + "=" * 60)
    print("  全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
