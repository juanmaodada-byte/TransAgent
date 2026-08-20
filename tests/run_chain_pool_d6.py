"""
D6 共享池链路演示（真实LLM）
============================
Vibe Coder A | 2026-08-15 (D6)

按共享池路径跑三个 Sub-Agent，逐阶段打印输出：
    译前（策略+术语）→ 译中（初译+句对齐）→ 译后（质检+润色）

运行：
    cd "d:\\Side Projects\\Developing\\TransAgent"
    set DEEPSEEK_API_KEY=sk-xxx
    python -X utf8 -m transagent.tests.run_chain_pool_d6
"""

import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import Chunk, PreprocessResult, UserPrefs
from transagent.backend.core.shared_pool import SharedPool
from transagent.backend.core.pre_agent import spawn_pre_translate
from transagent.backend.core.translate_agent import spawn_translate
from transagent.backend.core.post_agent import spawn_post_translate
from transagent.backend.pipeline.aligner import align_chunks

TEST_TEXT = (
    "Cloud computing and microservices transform financial services by offering scalable, "
    "affordable, and secure solutions for core functions. This paper explores how financial "
    "institutions can make the most of Infrastructure as a Service (IaaS), Platform as a Service "
    "(PaaS), and Software as a Service (SaaS) models to manage large volumes of sensitive data, "
    "improve fraud detection systems, and streamline compliance with evolving regulations. "
    "This paper proposes a cloud-native architecture emphasizing microservices design principles, "
    "modularity, and independent deployment to increase agility, reduce operational overhead, and "
    "foster rapid innovation. This paper demonstrates significant cost savings, tighter security "
    "controls, and faster time-to-market for new banking features through a comparative analysis of "
    "real-world case studies. Decoupling monolithic applications into more minor services enables "
    "financial organizations to experiment, test, and deploy upgrades without disrupting "
    "mission-critical transactions. Ultimately, the synergy between cloud computing and microservices "
    "enables financial institutions to provide enhanced customer experience, stay competitive, and "
    "attain sustainable growth within a highly regulated industry."
)


def _load_test_text() -> str:
    """测试文本来源：命令行参数（文件路径 或 直接文本）→ 缺省内置金融文本。"""
    if len(sys.argv) > 1:
        p = sys.argv[1]
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
        return sys.argv[1].strip()
    return TEST_TEXT


def _line(c="="):
    print(c * 70)


def _print_strategy(sb):
    print(f"  ICT子领域    : {sb.ict_domain} ({sb.domain_confidence})")
    print(f"  难度         : {sb.difficulty}")
    print(f"  风格         : {sb.style}")
    print(f"  直译/意译比例: {sb.literal_ratio}")
    print(f"  目标读者     : {sb.target_audience}")
    print(f"  规则         : {sb.rules}")
    print(f"  翻译方向     : {sb.direction}")
    if sb.analysis_notes:
        print(f"  判断依据     : {sb.analysis_notes}")


def _print_terms(tt):
    if not tt or (not tt.entries and not tt.pending_entries):
        print("  （无术语）")
        return
    for e in tt.entries:
        tag = "【不译】" if e.action == "notranslate" else ""
        print(f"  - {e.term} → {e.translation} {tag}[{e.confidence}·{e.source}]")
    for e in tt.pending_entries:
        print(f"  - (待确认) {e.term} → {e.translation} [{e.confidence}·{e.source}]")


def _print_qa(qa):
    print(f"  总分        : {qa.total_score}/10")
    print(f"  术语准确性  : {qa.term_accuracy} (30%)")
    print(f"  语义忠实度  : {qa.semantic_fidelity} (30%)")
    print(f"  代码完整性  : {qa.code_integrity} (15%)")
    print(f"  流畅性      : {qa.fluency} (15%)")
    print(f"  风格匹配    : {qa.style_match} (10%)")
    print(f"  总结        : {qa.summary}")
    if not qa.issues:
        print("  问题列表    : 无")
        return
    print(f"  问题列表    : {len(qa.issues)}项")
    for i, iss in enumerate(qa.issues, 1):
        print(f"  ── [{i}] {iss.id} · {iss.location} · [{iss.severity}] {iss.type}（{iss.nature}）"
              f"{' · 必须修复' if iss.must_fix else ' · 非强制'}")
        print(f"      chunk_id  : {iss.chunk_id} | pair_index: {iss.pair_index}")
        if iss.source_seg:
            print(f"      源句      : {iss.source_seg}")
        if iss.target_seg:
            print(f"      译句      : {iss.target_seg}")
        if iss.current:
            print(f"      当前      : {iss.current}")
        if iss.suggestion:
            print(f"      建议      : {iss.suggestion}")
        if iss.description:
            print(f"      说明      : {iss.description}")


async def main():
    print()
    _line()
    print("  D6 共享池链路演示（真实LLM）")
    _line()

    # ── 测试文本（命令行参数 或 内置）──
    text = _load_test_text()

    # ── 构建共享池 ──
    chunk = Chunk(chunk_id="chunk_1", source_text=text, token_estimate=0,
                  heading_path=[], order=1)
    pool = SharedPool(session_id="chain_demo")
    pool.source_md = text
    pool.chunks = [chunk]
    pool.preprocess_result = PreprocessResult(protected_md=text, chunks=[chunk],
                                              placeholder_map=None,
                                              token_estimate_total=0, chunk_count=1)
    pool.user_prefs = UserPrefs(user_id="demo_user", default_style="technical")
    print(f"\n输入：{len(text)}字符 · 1 chunk · en→zh")

    # ── Step 1 译前 ──
    _line("-")
    print("【译前 Sub-Agent】策略制定 → 术语提取")
    _line("-")
    await spawn_pre_translate(pool)
    print("\n① 翻译策略书：")
    _print_strategy(pool.strategy_book)
    print("\n② 项目术语表（共%d条）:" % (pool.term_table.total_count if pool.term_table else 0))
    _print_terms(pool.term_table)

    # ── Step 2 译中 ──
    _line("-")
    print("【译中 Sub-Agent】主译 → 一致性")
    _line("-")
    await spawn_translate(pool)
    print("\n③ 初译稿（%d字符）：" % len(pool.draft))
    print(pool.draft)
    cr = pool.consistency_report
    print(f"\n   一致性: 预检{'通过' if cr.precheck_passed else f'发现问题{cr.issues_found}处'} | "
          f"LLM修复{'触发' if cr.llm_fix_triggered else '未触发'}")

    # ── Step 2.5 初译稿对齐（进池子即做·供质检定位）──
    pool.aligned_pairs = align_chunks(pool.chunks, pool.chunk_drafts)
    print(f"\n④ 句对齐: {len(pool.aligned_pairs)}个句对")
    for i, p in enumerate(pool.aligned_pairs):
        print(f"   P{i+1} | 源: {p.source_seg}")
        print(f"        | 译: {p.target_seg}")

    # ── Step 3 译后 ──
    _line("-")
    print("【译后 Sub-Agent】质检 → 润色")
    _line("-")
    await spawn_post_translate(pool)
    print("\n⑤ 质检报告：")
    _print_qa(pool.qa_report)
    print(f"\n⑥ 润色说明: {pool.polish_notes}")
    print("\n⑦ 终稿（%d字符）：" % len(pool.final_text))
    print(pool.final_text)

    # ── 池子审计 ──
    _line("-")
    print("【共享池审计】谁产出 / 谁消费")
    _line("-")
    for artifact, producers in sorted(pool.providers.items()):
        consumers = pool.consumers.get(artifact, set())
        producers_str = ",".join(sorted(producers))
        consumers_str = ",".join(sorted(consumers))
        tail = f" → 被 {consumers_str} 消费" if consumers else ""
        print(f"  {artifact:16s} 由 {producers_str:16s} 产出{tail}")
    _line("=")
    print("  链路完成 ✅")
    _line("=")


if __name__ == "__main__":
    asyncio.run(main())
