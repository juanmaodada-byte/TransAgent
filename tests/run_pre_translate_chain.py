"""
PreAgent → TranslateAgent 链路测试脚本
======================================
只跑译前 + 译中两个Sub-Agent，逐级打印每个Agent的完整输出。

用法（从包根目录 d:\\Side Projects\\Developing\\TransAgent 运行）：
    set DEEPSEEK_API_KEY=sk-xxx
    python -X utf8 transagent/tests/run_pre_translate_chain.py
"""

import sys, io, asyncio, json, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 强制刷新 config 单例以读取环境变量
from transagent.backend.config import reset_config, get_config
reset_config()
cfg = get_config()

from transagent.interface import (
    PreprocessResult, Chunk, PlaceholderMap, UserPrefs, TermTable, StrategyBook,
)

# ── 测试文本 ──────────────────────────────────────────────────────────

TEST_TEXT = """Existing research on cloud computing for financial services spans
academic studies and industry frameworks. For example, the
AWS Cloud Adoption Framework for Financial Services and
IBM Cloud for Financial Services offer architectural blueprints
and best practices for designing secure, scalable environments in
regulated industries [6,7]."""

DIRECTION = "en_to_zh"   # 测试文本为英文 → 中文


# ── 工具 ─────────────────────────────────────────────────────────────

SEP = "=" * 70
SUB = "-" * 50

DIR_LABEL = "ZH → EN" if DIRECTION == "zh_to_en" else "EN → ZH"


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
    print(f"  PreAgent → TranslateAgent 链路测试 | 方向: {DIR_LABEL}")
    print(SEP)
    print(f"  Model : {cfg.llm.primary_model}")
    print(f"  API   : {cfg.llm.primary_base_url}")
    print(f"  Key   : {cfg.llm.primary_api_key[:12]}...")

    # ── Step 0: 准备输入 ─────────────────────────────────────────────
    preprocess = build_mock_preprocess_result(TEST_TEXT)
    user_prefs = UserPrefs(
        user_id="demo_user",
        default_style="technical",
        domain_tags=["云计算", "微服务", "金融"],
    )

    print("\n" + SUB)
    print("  >>> 输入")
    print(SUB)
    print(f"  原文（{len(TEST_TEXT)} chars）:")
    print(f"  ---")
    for line in TEST_TEXT.split("\n"):
        print(f"  | {line}")
    print(f"  ---")

    # ── Step 1: 译前 PreAgent ────────────────────────────────────────
    print("\n" + SEP)
    print("  Step 1/2: spawn_pre_translate() — 策略制定 + 术语提取")
    print(SEP)
    t0 = time.time()

    from transagent.backend.core.pre_agent import spawn_pre_translate

    pre_result = await spawn_pre_translate(preprocess, user_prefs, direction=DIRECTION)
    elapsed = time.time() - t0

    sb: StrategyBook = pre_result.strategy_book
    tt: TermTable = pre_result.term_table

    print("\n" + SUB)
    print("  >>> PreAgent 产出: StrategyBook（翻译策略）")
    print(SUB)
    print(f"  ICT Domain      : {sb.ict_domain} (confidence={sb.domain_confidence})")
    print(f"  Difficulty      : {sb.difficulty}")
    print(f"  Style           : {sb.style}")
    print(f"  Literal Ratio   : {sb.literal_ratio}")
    print(f"  Target Audience : {sb.target_audience}")
    print(f"  Rules           : {json.dumps(sb.rules, ensure_ascii=False)}")

    print("\n" + SUB)
    print("  >>> PreAgent 产出: TermTable（术语表）")
    print(SUB)
    print(f"  Total terms    : {tt.total_count}")
    print(f"  RAG hits       : {tt.rag_hit_count}")
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
        print(f"\n  待确认 (pending):")
        for e in tt.pending_entries:
            print(f"    - {e.term} -> {e.translation} ({e.confidence}, {e.source})")

    print(f"\n  [OK] PreAgent 完成，耗时 {elapsed:.1f}s")

    # ── Step 2: 译中 TranslateAgent ─────────────────────────────────
    print("\n" + SEP)
    print(f"  Step 2/2: spawn_translate() — 主译 ({DIR_LABEL})")
    print(SEP)
    t0 = time.time()

    from transagent.backend.core.translate_agent import spawn_translate

    translate_result = await spawn_translate(
        chunks=pre_result.chunks,
        term_table=tt or TermTable(),
        strategy_book=sb or StrategyBook(),
        tm_refs=None,
        direction=DIRECTION,
    )
    elapsed = time.time() - t0

    print("\n" + SUB)
    print("  >>> TranslateAgent 产出: TranslateResult（初译稿）")
    print(SUB)
    print(f"  Draft length   : {len(translate_result.draft)} chars")
    print(f"  TM refs used   : {translate_result.tm_refs_used}")
    cr = translate_result.consistency_report
    if cr:
        print(f"  Consistency    : precheck_passed={cr.precheck_passed}, "
              f"issues={cr.issues_found}, llm_fix={cr.llm_fix_triggered}")
        if cr.details:
            for d in cr.details:
                print(f"    - [{d.get('type')}] chunk={d.get('chunk')} "
                      f"term={d.get('term','')} {d.get('note','')}")
    print(f"\n  译文全文:")
    print(f"  ---")
    for line in translate_result.draft.split("\n"):
        print(f"  | {line}")
    print(f"  ---")

    print(f"\n  [OK] TranslateAgent 完成，耗时 {elapsed:.1f}s")

    # ── 总结 ─────────────────────────────────────────────────────────
    print("\n" + SEP)
    print("  链路总结")
    print(SEP)
    print(f"  PreAgent ({len(tt.entries)} terms + {len(tt.pending_entries)} pending)")
    print(f"    → TranslateAgent ({len(translate_result.draft)} chars draft)")
    print(f"\n  >>> PreAgent → TranslateAgent 链路执行完成 <<<")


if __name__ == "__main__":
    asyncio.run(main())
