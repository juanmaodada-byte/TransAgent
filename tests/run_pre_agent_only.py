"""
PreAgent 单独测试脚本（D5技能化版·跳过RAG）
=========================================
只跑译前Sub-Agent（spawn_pre_translate），完整打印 策略书 + 术语表。

RAG匹配说明：cfg.rag_verification_enabled 默认 False → 直接跳过RAG查证，纯LLM链路。

用法（从包根目录 d:\\Side Projects\\Developing\\TransAgent 运行）：
    set DEEPSEEK_API_KEY=sk-xxx
    python -X utf8 -m transagent.tests.run_pre_agent_only
"""

import sys, io, asyncio, json, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 强制刷新 config 单例以读取环境变量
from transagent.backend.config import reset_config, get_config
reset_config()
cfg = get_config()

from transagent.interface import (
    PreprocessResult, Chunk, PlaceholderMap, UserPrefs,
)

# ── 测试文本（英文 → 中文方向）────────────────────────────────────────

TEST_TEXT = """A reference architecture that leverages cloud computing and
microservices to enable real-time transaction processing, improved
fault tolerance, and overall agility in financial applications."""

DIRECTION = "en_to_zh"   # 测试文本为英文 → 中文


# ── 工具 ─────────────────────────────────────────────────────────────

SEP = "=" * 70
SUB = "-" * 50


def build_mock_preprocess_result(source_text: str) -> PreprocessResult:
    """构造一个模拟的预处理结果（1 chunk）"""
    chunk = Chunk(
        chunk_id="chunk_001",
        source_text=source_text,
        token_estimate=len(source_text) // 2,
        heading_path=[],
        order=1,
    )
    return PreprocessResult(
        protected_md=source_text,
        chunks=[chunk],
        placeholder_map=PlaceholderMap(nt_count=0, t_count=0),
        token_estimate_total=len(source_text) // 2,
        chunk_count=1,
    )


# ── 主流程 ───────────────────────────────────────────────────────────

async def main():
    print(SEP)
    print(f"  PreAgent（D5技能化）单独测试 | 方向: EN → ZH | RAG: 跳过")
    print(SEP)
    print(f"  Model : {cfg.llm.primary_model}")
    print(f"  API   : {cfg.llm.primary_base_url}")
    print(f"  Key   : {cfg.llm.primary_api_key[:12]}...")
    print(f"  RAG开关: rag_verification_enabled = {cfg.pipeline.rag_verification_enabled}")

    # ── 准备输入 ─────────────────────────────────────────────────────
    preprocess = build_mock_preprocess_result(TEST_TEXT)
    user_prefs = UserPrefs(
        user_id="demo_user",
        default_style="technical",
        domain_tags=["云计算", "微服务", "金融"],
    )

    print("\n" + SUB)
    print("  >>> 输入")
    print(SUB)
    print(f"  原文（{len(TEST_TEXT)} chars，1 chunk）:")
    print(f"  ---")
    for line in TEST_TEXT.split("\n"):
        print(f"  | {line}")
    print(f"  ---")

    # ── 调用 PreAgent ───────────────────────────────────────────────
    print("\n" + SEP)
    print("  spawn_pre_translate() — 策略制定(技能一) → 术语提取(技能二)")
    print(SEP)
    t0 = time.time()

    from transagent.backend.core.pre_agent import spawn_pre_translate

    pre_result = await spawn_pre_translate(preprocess, user_prefs, direction=DIRECTION)
    elapsed = time.time() - t0

    sb = pre_result.strategy_book
    tt = pre_result.term_table

    # ── 策略书 ──────────────────────────────────────────────────────
    print("\n" + SUB)
    print("  >>> 技能一产出: StrategyBook（翻译策略）")
    print(SUB)
    print(f"  ICT Domain      : {sb.ict_domain} (confidence={sb.domain_confidence})")
    print(f"  Difficulty      : {sb.difficulty}")
    print(f"  Style           : {sb.style}")
    print(f"  Literal Ratio   : {sb.literal_ratio}")
    print(f"  Target Audience : {sb.target_audience}")
    print(f"  Direction       : {sb.direction}")
    print(f"  Rules           : {json.dumps(sb.rules, ensure_ascii=False)}")
    print(f"  Analysis Notes  : {sb.analysis_notes}")

    # ── 术语表 ──────────────────────────────────────────────────────
    print("\n" + SUB)
    print("  >>> 技能二产出: TermTable（术语表）")
    print(SUB)
    print(f"  Total terms    : {tt.total_count}")
    print(f"  RAG hits       : {tt.rag_hit_count}（开关关闭→应为0）")
    print(f"  Web search     : {tt.web_search_count}")
    print(f"  LLM generated  : {tt.llm_gen_count}")
    print(f"  Pending        : {len(tt.pending_entries)}")
    print(f"\n  正式术语 (entries):")
    if tt.entries:
        for e in tt.entries:
            action_tag = " [NOTRANSLATE]" if e.action == "notranslate" else ""
            print(f"    - {e.term} -> {e.translation} ({e.confidence}, {e.source}){action_tag}")
    else:
        print("    (无)")
    if tt.pending_entries:
        print(f"\n  待确认 (pending·low confidence):")
        for e in tt.pending_entries:
            print(f"    - {e.term} -> {e.translation} ({e.confidence}, {e.source})")

    print(f"\n  [OK] PreAgent 完成，耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
