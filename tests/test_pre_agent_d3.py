"""
译前Sub-Agent D3 单元测试（D5更新·D5输出规范化）
==============================================
Vibe Coder A | v1.2 | 2026-08-12 (D5)

不依赖真实LLM：monkeypatch 两个技能模块的 `chat`
（D5目录化后，LLM调用在 skills/strategy_formulation/scripts 与 skills/term_extraction/scripts 内），验证：
  1. 策略制定：LLM返回 → StrategyBook 解析；缺字段 → 重试纠正一次 → 仍缺兜底默认值
  2. 术语提取：三字段契约（term/translation/source）→ 系统派生 domain/confidence/action；
     term_table(medium) → entries，pending_terms(low) → pending_entries
  3. source 诚实化：无RAG无搜索时统一 "LLM生成"，不虚标 "Web搜索"
  4. 去重：重复术语只保留一条
  5. 空术语过滤
  6. RAG开关关闭时不调用知识库（search_rag 零调用）
  7. 方向路由：中文文本自动检测 → zh_to_en，英文文本 → en_to_zh
  8. ZH→EN 提取：嵌入英文词 notranslate 保留原文，中文术语给英文译法
  9. action 派生：译法=原文 → notranslate（如 API key→API key）

运行：
    cd "d:\Side Projects\Developing\TransAgent"
    python -X utf8 -m transagent.tests.test_pre_agent_d3
"""

import asyncio
import json
import sys

# 不用 io.TextIOWrapper(sys.stdout.buffer) 包装(共享底层 buffer 在重定向/GC 时会
# 报 "I/O operation on closed file"),改就地 reconfigure——无新包装器、无 buffer 共享。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import (
    PreprocessResult, Chunk, PlaceholderMap, UserPrefs,
    PreTranslateResult, TermTable,
)
from transagent.backend.core import pre_agent as pa
from transagent.backend.core.skills.pre_skills.strategy_formulation.scripts import strategy_skill
from transagent.backend.core.skills.pre_skills.term_extraction.scripts import term_skill

# ── D6 整合：config 默认已开启 RAG，本套件非 RAG 专项测试须确定性运行。
#    模块级把 search_rag 打成快速空命中（不触发 bge-m3 加载/网络），
#    RAG-ON 的真实接线验证见 test_08（单独 mock 命中）。
from transagent.backend.knowledge import rag_terms as _rag_mod


def _no_rag(*a, **kw):
    return []


_rag_mod.search_rag = _no_rag

# ── 测试数据 ────────────────────────────────────────────────────────

FAKE_STRATEGY = {
    "ict_domain": "Kubernetes/云原生",
    "domain_confidence": "high",
    "difficulty": "medium",
    "style": "technical",
    "literal_ratio": 0.6,
    "target_audience": "开发者",
    "rules": {"code": "notranslate", "tone": "professional",
              "sentence_length": "medium", "voice": "active"},
    "analysis_notes": "K8s部署指南，含YAML示例，属技术文档",
}

FAKE_TERMS = {
    # 三字段契约：term/translation/source（domain/confidence/action 由系统派生）
    "term_table": [
        {"term": "rolling update", "translation": "滚动更新", "source": "RAG命中"},
        {"term": "Deployment", "translation": "Deployment", "source": "RAG命中"},
        {"term": "readiness probe", "translation": "就绪探针", "source": "Web搜索"},
        # 重复术语（去重测试）
        {"term": "rolling update", "translation": "滚动更新", "source": "RAG命中"},
        # 空术语（过滤测试）
        {"term": "", "translation": "", "source": "LLM生成"},
        # 普通词（LLM误报·仍保留但标LLM生成）
        {"term": "network", "translation": "网络", "source": "Web搜索"},
    ],
    "pending_terms": [
        {"term": "GitOps pipeline", "translation": "GitOps流水线", "source": "LLM生成"},
        {"term": "canary deployment", "translation": "金丝雀发布（自拟）", "source": "LLM生成"},
    ],
}

# ── 计数器：记录 chat 被调用了多少次、传了什么prompt ──
CALLS = []


def make_mock_chat():
    async def mock_chat(system_prompt, user_message, **kwargs):
        CALLS.append({"system": system_prompt, "user": user_message, "kwargs": kwargs})
        # 术语Prompt的特征串是"术语专家"（策略Prompt中不含）
        if "术语专家" in system_prompt:
            return FAKE_TERMS
        return FAKE_STRATEGY
    return mock_chat


def build_preprocess() -> PreprocessResult:
    chunk = Chunk(
        chunk_id="chunk_001",
        source_text="## Rolling Update\n\nA rolling update allows zero downtime. "
                    "Use kubectl to monitor progress.",
        token_estimate=40,
        heading_path=["## Rolling Update"],
        order=1,
    )
    return PreprocessResult(
        protected_md=chunk.source_text,
        chunks=[chunk],
        placeholder_map=PlaceholderMap(nt_count=0, t_count=0),
        token_estimate_total=40,
        chunk_count=1,
    )


# ── 测试用例 ────────────────────────────────────────────────────────

async def test_01_strategy_parsing():
    print("\n[1/5] 策略制定：LLM返回 → StrategyBook 解析")
    strategy_skill.chat = term_skill.chat = make_mock_chat()
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    sb = result.strategy_book
    assert sb.ict_domain == "Kubernetes/云原生", sb
    assert sb.difficulty == "medium"
    assert sb.literal_ratio == 0.6
    assert sb.rules["code"] == "notranslate"
    print(f"  OK: ict_domain={sb.ict_domain} | literal_ratio={sb.literal_ratio} | rules={sb.rules}")


async def test_02_confidence_split():
    print("\n[2/5] 术语分流：high/medium → entries，low → pending")
    strategy_skill.chat = term_skill.chat = make_mock_chat()
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    tt: TermTable = result.term_table
    terms = {e.term: e for e in tt.entries}
    pendings = {e.term: e for e in tt.pending_entries}
    assert "rolling update" in terms, "high术语应进entries"
    assert "readiness probe" in terms, "medium术语应进entries"
    assert "GitOps pipeline" in pendings, "low术语应进pending"
    assert "canary deployment" in pendings
    assert "network" in terms
    print(f"  OK: entries={len(tt.entries)}个 pending={len(tt.pending_entries)}个 total={tt.total_count}")


async def test_03_source_honesty():
    print("\n[3/5] source诚实化：无RAG无搜索 → 全部标'LLM生成'")
    strategy_skill.chat = term_skill.chat = make_mock_chat()
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    tt = result.term_table
    sources = set(e.source for e in tt.entries + tt.pending_entries)
    assert sources == {"LLM生成"}, f"source应全为LLM生成，实际={sources}"
    assert tt.rag_hit_count == 0
    assert tt.web_search_count == 0
    assert tt.llm_gen_count == len(tt.entries) + len(tt.pending_entries)
    print(f"  OK: sources={sources} | rag={tt.rag_hit_count} web={tt.web_search_count} llm={tt.llm_gen_count}")


async def test_04_dedup_and_empty():
    print("\n[4/5] 去重 + 空术语过滤")
    strategy_skill.chat = term_skill.chat = make_mock_chat()
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    tt = result.term_table
    all_terms = [e.term for e in tt.entries + tt.pending_entries]
    assert len(all_terms) == len(set(all_terms)), f"存在重复术语: {all_terms}"
    assert "" not in all_terms, "空术语未被过滤"
    assert all_terms.count("rolling update") == 1
    print(f"  OK: 去重后共{len(all_terms)}个术语，无重复无空项")


async def test_05_rag_off_no_knowledge_query():
    print("\n[5/5] RAG开关关闭：术语提取不触发知识库查询")
    # 行为验证：spawn_pre_translate 期间 search_rag 不应被调用
    from transagent.backend.knowledge import rag_terms as rag_mod
    from transagent.backend.config import get_config

    calls = {"n": 0}

    def _boom(*a, **kw):
        calls["n"] += 1
        raise AssertionError("RAG关闭时不应调用 search_rag")

    original = rag_mod.search_rag
    rag_mod.search_rag = _boom
    # D6 整合后 config 默认开启 RAG，此测试验证"关闭"路径 → 显式强制关闭并还原
    cfg = get_config().pipeline
    prev_flag = getattr(cfg, "rag_verification_enabled", False)
    cfg.rag_verification_enabled = False
    try:
        strategy_skill.chat = term_skill.chat = make_mock_chat()
        result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    finally:
        cfg.rag_verification_enabled = prev_flag
        rag_mod.search_rag = original

    assert calls["n"] == 0, f"search_rag被调用了{calls['n']}次"
    assert result.term_table.rag_hit_count == 0
    print("  OK: 全程0次search_rag调用（无bge-m3模型加载开销）")


async def test_06_direction_routing():
    print("\n[6/8] 方向路由：中文文本→zh_to_en，英文文本→en_to_zh")
    strategy_skill.chat = term_skill.chat = make_mock_chat()

    # 中文文本 → auto → zh_to_en
    CALLS.clear()
    zh_pre = build_preprocess()
    zh_pre.protected_md = "在创建 API key 之后，你可以使用样例脚本访问 DeepSeek 模型。"
    zh_pre.chunks[0].source_text = zh_pre.protected_md
    await pa.spawn_pre_translate(zh_pre, UserPrefs(user_id="u1"))
    zh_call = CALLS[-1]["user"]  # 最后一次调用是术语提取
    assert "翻译方向：中文 → 英文" in zh_call, f"中文文本应路由到zh_to_en，实际上下文={zh_call[:80]}"

    # 英文文本 → auto → en_to_zh
    CALLS.clear()
    await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    en_call = CALLS[-1]["user"]
    assert "翻译方向：英文 → 中文" in en_call, f"英文文本应路由到en_to_zh，实际上下文={en_call[:80]}"

    print("  OK: 自动检测正确路由（中文→zh_to_en / 英文→en_to_zh）")


async def test_07_zh_to_en_embedded_english():
    print("\n[7/8] ZH→EN：嵌入英文词 notranslate 保留原文，中文术语给英文译法")
    fake_zh_en = {
        "term_table": [
            {"term": "样例脚本", "translation": "sample script", "source": "LLM生成"},
            {"term": "API key", "translation": "API key", "source": "LLM生成"},
            {"term": "stream", "translation": "stream", "source": "LLM生成"},
        ],
        "pending_terms": [],
    }

    async def mock_zh_en(system_prompt, user_message, **kwargs):
        return fake_zh_en

    strategy_skill.chat = term_skill.chat = mock_zh_en
    zh_pre = build_preprocess()
    zh_pre.protected_md = "在创建 API key 之后，您可以将 stream 设置为 true。"
    zh_pre.chunks[0].source_text = zh_pre.protected_md
    result = await pa.spawn_pre_translate(zh_pre, UserPrefs(user_id="u1"), direction="zh_to_en")

    tt = result.term_table
    by_term = {e.term: e for e in tt.entries}
    assert by_term["样例脚本"].translation == "sample script"
    assert by_term["样例脚本"].action == "translate"
    assert by_term["API key"].action == "notranslate", "嵌入英文词应notranslate"
    assert by_term["API key"].translation == "API key", "嵌入英文词应保留原文"
    assert by_term["stream"].action == "notranslate"
    assert tt.total_count == 3
    print(f"  OK: {tt.total_count}条术语，嵌入英文词均保留原文notranslate")


async def test_08_rag_on_uses_kb_hits():
    print("\n[8/8] RAG开启：术语命中知识库 → 复用库译法 source=RAG命中")
    from transagent.backend.knowledge import rag_terms as rag_mod
    from transagent.backend.config import get_config
    from transagent.interface import TermEntry as ITermEntry

    # 模拟知识库命中：仅 "rolling update" 命中，库译法与 LLM 不同 → 应以库译法覆盖
    def fake_search_rag(term, user_id, domain="", top_k=None):
        if str(term).strip().lower() == "rolling update":
            return [ITermEntry(term="rolling update", translation="（库译法）滚动更新",
                               domain="kubernetes", confidence="high", action="translate",
                               source="RAG命中", user_id=user_id)]
        return []

    original = rag_mod.search_rag
    rag_mod.search_rag = fake_search_rag
    cfg = get_config().pipeline
    prev_flag = getattr(cfg, "rag_verification_enabled", False)
    cfg.rag_verification_enabled = True
    try:
        strategy_skill.chat = term_skill.chat = make_mock_chat()
        result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    finally:
        cfg.rag_verification_enabled = prev_flag
        rag_mod.search_rag = original

    tt = result.term_table
    entries = {e.term: e for e in tt.entries}
    # FAKE_TERMS 中 "rolling update" 出现两次(去重测试的重复项) → RAG 查证对每个候选各 +1,
    # 随后 entries 统一去重;这里只断言行为本质:命中数>=1、复用库译法、最终条目去重。
    assert tt.rag_hit_count >= 1, f"RAG命中应>=1，实际={tt.rag_hit_count}"
    assert "rolling update" in entries, "命中术语应进entries"
    assert entries["rolling update"].translation == "（库译法）滚动更新", \
        "命中术语应复用库译法（覆盖LLM译法）"
    assert entries["rolling update"].source == "RAG命中"
    assert entries["rolling update"].confidence == "high"
    assert [e.term for e in tt.entries].count("rolling update") == 1, "RAG命中后 entries 仍应去重"
    assert "readiness probe" in entries, "未命中术语仍走LLM分流"
    assert any(e.term == "GitOps pipeline" for e in tt.pending_entries), "low术语仍进pending"
    print(f"  OK: RAG命中{tt.rag_hit_count}个，rolling update 复用库译法（source=RAG命中·high）且去重")


async def test_09_strategy_missing_field_retry():
    print("\n[9/10] 策略书缺字段：带纠正信息重试一次，补全后才产出")
    calls = {"n": 0}

    async def mock_incomplete(system_prompt, user_message, **kwargs):
        calls["n"] += 1
        if "术语专家" in system_prompt:
            return FAKE_TERMS
        if calls["n"] == 1:
            # 第一次输出缺 ict_domain 和 analysis_notes
            incomplete = dict(FAKE_STRATEGY)
            incomplete.pop("ict_domain")
            incomplete.pop("analysis_notes")
            return incomplete
        return FAKE_STRATEGY  # 重试输出完整 → 应补全

    strategy_skill.chat = term_skill.chat = mock_incomplete
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    sb = result.strategy_book
    assert sb.ict_domain == "Kubernetes/云原生", "重试后应补全缺失字段"
    assert sb.analysis_notes == FAKE_STRATEGY["analysis_notes"]
    # 策略缺字段重试一次 → 调用序列：策略1 + 策略重试1 + 术语1 = 3
    assert calls["n"] == 3, f"缺字段应重试一次（共3次LLM调用），实际={calls['n']}"
    print(f"  OK: 缺字段重试后补全（LLM调用{calls['n']}次·ict_domain/analysis_notes补齐）")


async def test_10_strategy_missing_field_fallback():
    print("\n[10/10] 策略书重试仍缺字段：默认值兜底，不静默崩溃")
    calls = {"n": 0}

    async def mock_never_complete(system_prompt, user_message, **kwargs):
        calls["n"] += 1
        if "术语专家" in system_prompt:
            return FAKE_TERMS
        # 始终缺 ict_domain（重试也不补全）
        incomplete = dict(FAKE_STRATEGY)
        incomplete.pop("ict_domain")
        return incomplete

    strategy_skill.chat = term_skill.chat = mock_never_complete
    result = await pa.spawn_pre_translate(build_preprocess(), UserPrefs(user_id="u1"))
    sb = result.strategy_book
    assert sb.ict_domain == "其他", "兜底默认应使用 '其他'"
    assert sb.difficulty == "medium"
    assert sb.rules["code"] == "notranslate"
    # 策略2次（初+重试）都不完整 → 兜底；术语1次
    assert calls["n"] == 3, f"重试仍缺后兜底（共3次LLM调用），实际={calls['n']}"
    print(f"  OK: 重试仍缺字段 → 默认值兜底（ict_domain=其他·不崩溃）")


async def main():
    print("=" * 60)
    print("  译前Sub-Agent D3 单元测试（mock LLM）")
    print("=" * 60)
    await test_01_strategy_parsing()
    await test_02_confidence_split()
    await test_03_source_honesty()
    await test_04_dedup_and_empty()
    await test_05_rag_off_no_knowledge_query()
    await test_06_direction_routing()
    await test_07_zh_to_en_embedded_english()
    await test_08_rag_on_uses_kb_hits()
    await test_09_strategy_missing_field_retry()
    await test_10_strategy_missing_field_fallback()
    print("\n" + "=" * 60)
    print("  全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
