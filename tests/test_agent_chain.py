"""
Sub-Agent 通信链路测试
======================
D1 验证脚本：跳过 pipeline + knowledge，直接用 mock 数据
测试 PreAgent → TranslateAgent → PostAgent 三级 LLM 调用链路。

支持 EN→ZH 和 ZH→EN 双向翻译。

用法：
    cd transagent
    set DEEPSEEK_API_KEY=sk-xxx
    python -X utf8 tests/test_agent_chain.py              # ZH→EN (默认)
    python -X utf8 tests/test_agent_chain.py en_to_zh     # EN→ZH
"""

import sys, io, asyncio, os, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 强制刷新 config 单例以读取环境变量
from transagent.backend.config import reset_config, get_config
reset_config()

from transagent.interface import (
    PreprocessResult, PreTranslateResult, TranslateResult, PostTranslateResult,
    Chunk, PlaceholderMap, UserPrefs, TermTable, TermEntry, StrategyBook, TMEntry,
)

# ── 测试文本 ──────────────────────────────────────────────────────────

TEXT_EN_TO_ZH = """## Deploying a Rolling Update

A rolling update allows you to update your application with zero downtime.
Kubernetes achieves this by incrementally replacing old Pods with new ones,
ensuring the Service always points to healthy instances.

### Key Concepts

1. **Deployment**: Controls the rollout of new ReplicaSets. You can pause,
   resume, or rollback a deployment at any time.
2. **Pod**: The smallest deployable unit in Kubernetes. Each Pod encapsulates
   one or more containers that share networking and storage.
3. **Readiness Probe**: Kubernetes uses readiness probes to know when a
   container is ready to start accepting traffic. Without it, traffic may
   be sent to unready Pods.

### Configuration Example

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1
```

This configuration ensures at most one Pod can be unavailable during
the update, while one extra Pod can be created temporarily.

### Best Practices

- Always define a readiness probe to prevent traffic from reaching
  uninitialized containers.
- Use `kubectl rollout status` to monitor the progress of a deployment.
- Set `minReadySeconds` to allow the Pod to stabilize before marking
  the rollout as successful.
- For stateful workloads, consider using StatefulSets instead of Deployments.
"""

TEXT_ZH_TO_EN = "请将此 API key 保存在安全且易于访问的地方。出于安全原因，你将无法通过 API keys 管理界面再次查看它。如果你丢失了这个 key，将需要重新创建。"


# ── 格式化工具 ───────────────────────────────────────────────────────

SEP = "=" * 70
SUB = "-" * 50


def print_header(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def print_section(title: str):
    print(f"\n{SUB}")
    print(f"  >>> {title}")
    print(SUB)


def build_mock_preprocess_result(source_text: str, heading: str = "") -> PreprocessResult:
    """构造一个模拟的预处理结果"""
    chunk = Chunk(
        chunk_id="chunk_001",
        source_text=source_text,
        token_estimate=len(source_text) // 2,
        heading_path=[heading] if heading else [],
        order=1,
    )
    return PreprocessResult(
        protected_md=source_text,
        chunks=[chunk],
        placeholder_map=PlaceholderMap(nt_count=0, t_count=0),
        token_estimate_total=len(source_text) // 2,
        chunk_count=1,
    )


# ── 主测试流程 ───────────────────────────────────────────────────────

async def test_agent_chain(direction: str = "zh_to_en"):
    cfg = get_config()

    if direction == "zh_to_en":
        source_text = TEXT_ZH_TO_EN
        heading = ""
        user_domain_tags = ["API/开发者工具", "云平台"]
        dir_label = "ZH → EN"
    else:
        source_text = TEXT_EN_TO_ZH
        heading = "## Deploying a Rolling Update"
        user_domain_tags = ["Kubernetes/云原生", "CI/CD"]
        dir_label = "EN → ZH"

    print_header(f"TransAgent Sub-Agent 通信链路测试 | 方向: {dir_label}")
    print(f"  Model : {cfg.llm.primary_model}")
    print(f"  API   : {cfg.llm.primary_base_url}")
    print(f"  Key   : {cfg.llm.primary_api_key[:12]}...")

    # ── Step 0: 准备输入 ─────────────────────────────────────────────
    preprocess = build_mock_preprocess_result(source_text, heading)
    user_prefs = UserPrefs(
        user_id="demo_user",
        default_style="technical",
        domain_tags=user_domain_tags,
    )

    print_section("Input: PreprocessResult (mock)")
    print(f"  Chunks     : {len(preprocess.chunks)}")
    print(f"  Tokens est : {preprocess.token_estimate_total}")
    print(f"  Source text:")
    for line in source_text.split("\n")[:5]:
        print(f"    | {line}")
    if len(source_text.split("\n")) > 5:
        print(f"    | ... ({len(source_text)} chars total)")

    # ── Step 1: 译前 Sub-Agent ────────────────────────────────────────
    print_header("Step 1/3: spawn_pre_translate() — 策略制定 + 术语提取")
    t0 = time.time()

    from transagent.backend.core.pre_agent import spawn_pre_translate

    try:
        pre_result: PreTranslateResult = await spawn_pre_translate(preprocess, user_prefs)
        elapsed = time.time() - t0
    except Exception as e:
        print(f"\n  [FAIL] spawn_pre_translate() 异常: {e}")
        import traceback; traceback.print_exc()
        return

    sb = pre_result.strategy_book
    if sb:
        print_section("产出: StrategyBook")
        print(f"  ICT Domain      : {sb.ict_domain} (confidence={sb.domain_confidence})")
        print(f"  Difficulty      : {sb.difficulty}")
        print(f"  Style           : {sb.style}")
        print(f"  Literal Ratio   : {sb.literal_ratio}")
        print(f"  Target Audience : {sb.target_audience}")
        print(f"  Rules           : {json.dumps(sb.rules, ensure_ascii=False)}")

    tt = pre_result.term_table
    if tt:
        print_section("产出: TermTable")
        print(f"  Total terms    : {tt.total_count}")
        print(f"  RAG hits       : {tt.rag_hit_count}")
        print(f"  Web search     : {tt.web_search_count}")
        print(f"  LLM generated  : {tt.llm_gen_count}")
        print(f"  Pending        : {len(tt.pending_entries)}")
        if tt.entries:
            print(f"\n  Confirmed terms:")
            for e in tt.entries[:10]:
                action_tag = " [NOTRANSLATE]" if e.action == "notranslate" else ""
                print(f"    - {e.term} -> {e.translation} ({e.confidence}, {e.source}){action_tag}")
        if tt.pending_entries:
            print(f"\n  Pending (low confidence):")
            for e in tt.pending_entries[:5]:
                print(f"    - {e.term} -> {e.translation} ({e.source})")

    print(f"\n  [OK] spawn_pre_translate() completed in {elapsed:.1f}s")

    # ── Step 2: 译中 Sub-Agent ────────────────────────────────────────
    print_header(f"Step 2/3: spawn_translate() — 主译 ({dir_label})")
    t0 = time.time()

    from transagent.backend.core.translate_agent import spawn_translate

    try:
        translate_result: TranslateResult = await spawn_translate(
            chunks=pre_result.chunks,
            term_table=tt or TermTable(),
            strategy_book=sb or StrategyBook(),
            tm_refs=None,
            direction=direction,
        )
        elapsed = time.time() - t0
    except Exception as e:
        print(f"\n  [FAIL] spawn_translate() 异常: {e}")
        import traceback; traceback.print_exc()
        return

    print_section("产出: TranslateResult")
    draft_preview = translate_result.draft[:500] if translate_result.draft else "(empty)"
    print(f"  Draft length     : {len(translate_result.draft)} chars")
    print(f"  TM refs used     : {translate_result.tm_refs_used}")
    if translate_result.consistency_report:
        cr = translate_result.consistency_report
        print(f"  Consistency check: precheck_passed={cr.precheck_passed}, "
              f"issues={cr.issues_found}, llm_fix={cr.llm_fix_triggered}")
    print(f"\n  Draft text:")
    print(f"  ---")
    for line in translate_result.draft.split("\n")[:20]:
        print(f"  | {line}")
    print(f"  ---")

    print(f"\n  [OK] spawn_translate() completed in {elapsed:.1f}s")

    # ── Step 3: 译后 Sub-Agent ────────────────────────────────────────
    print_header(f"Step 3/3: spawn_post_translate() — 质检 + 润色 ({dir_label})")
    t0 = time.time()

    from transagent.backend.core.post_agent import spawn_post_translate

    try:
        post_result: PostTranslateResult = await spawn_post_translate(
            source_md=preprocess.protected_md,
            draft=translate_result.draft,
            term_table=tt or TermTable(),
            strategy_book=sb or StrategyBook(),
            direction=direction,
        )
        elapsed = time.time() - t0
    except Exception as e:
        print(f"\n  [FAIL] spawn_post_translate() 异常: {e}")
        import traceback; traceback.print_exc()
        return

    qa = post_result.qa_report
    if qa:
        print_section("产出: QA Report")
        print(f"  Total Score       : {qa.total_score}/10")
        print(f"  Term Accuracy     : {qa.term_accuracy}  (30%)")
        print(f"  Semantic Fidelity : {qa.semantic_fidelity}  (30%)")
        print(f"  Code Integrity    : {qa.code_integrity}  (15%)")
        print(f"  Fluency           : {qa.fluency}  (15%)")
        print(f"  Style Match       : {qa.style_match}  (10%)")
        print(f"  Issues found      : {len(qa.issues)}")
        print(f"  Summary           : {qa.summary}")
        if qa.issues:
            for iss in qa.issues:
                print(f"    [{iss.severity}] {iss.location}: {iss.type} — {iss.description}")

    print_section("产出: Final Text (润色后)")
    print(f"  Length      : {len(post_result.final_text)} chars")
    print(f"  Polish notes: {post_result.polish_notes}")
    print(f"\n  Final text:")
    print(f"  ---")
    for line in post_result.final_text.split("\n")[:15]:
        print(f"  | {line}")
    print(f"  ---")

    print(f"\n  [OK] spawn_post_translate() completed in {elapsed:.1f}s")

    # ── 总结 ──────────────────────────────────────────────────────────
    print_header("链路测试总结")
    print(f"  Direction: {dir_label}")
    print(f"  PreAgent  -> TranslateAgent -> PostAgent")
    print(f"    StrategyBook     Draft              Final + QA")
    print(f"    ({len(tt.entries) if tt else 0} terms)          "
          f"({len(translate_result.draft)} chars)         "
          f"({len(post_result.final_text)} chars)")
    print(f"\n  >>> 三级 Sub-Agent 通信链路验证通过！<<<")


if __name__ == "__main__":
    direction = sys.argv[1] if len(sys.argv) > 1 else "zh_to_en"
    if direction not in ("en_to_zh", "zh_to_en"):
        print(f"用法: python test_agent_chain.py [en_to_zh|zh_to_en]")
        sys.exit(1)
    asyncio.run(test_agent_chain(direction))
