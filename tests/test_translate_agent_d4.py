"""
译中Sub-Agent D4 单元测试（D5更新）
==================================
Vibe Coder A | v1.1 | 2026-08-10 (D5)

不依赖真实LLM：monkeypatch 两个技能模块的 `chat`
（D5技能化后，LLM调用在 translate_skills/chunk_translate_skill.py 与 consistency_skill.py 内），验证：
  1. 主译Prompt调优：EN→ZH 使用 TRANSLATE_EN_ZH_PROMPT（含few-shot示例·术语强制·占位符双保护·Markdown保留）
  2. ZH→EN 方向路由：使用 TRANSLATE_ZH_EN_PROMPT
  3. 串行翻译上下文传递：后一chunk携带前一chunk译文（前文翻译）
  4. 一致性预检·占位符缺失（{NT_n}/{T_n}双保护）→ 触发LLM修复
  5. 一致性预检·术语未翻译（translate术语保留源语言原文）→ 触发修复
  6. 一致性预检·notranslate术语被改译 → 触发修复
  7. 一致性预检·代码块围栏不匹配 → 触发修复
  8. 全部一致 → precheck_passed=True·零额外LLM调用
  9. 并行chunk翻译：顺序保留·每chunk独立携带策略+术语
  10. chunk翻译失败降级：失败chunk标"[翻译失败]"·不影响其余chunk
  11. Prompt质量：术语表/占位符/Markdown/翻译腔规则均写入系统提示

运行：
    cd "d:\Side Projects\Developing\TransAgent"
    python -X utf8 -m transagent.tests.test_translate_agent_d4
"""

import asyncio
import io
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import (
    Chunk, TermTable, TermEntry, StrategyBook,
)
from transagent.backend.core import translate_agent as ta
from transagent.backend.core.skills.translate_skills.chunk_translate.scripts import chunk_translate_skill
from transagent.backend.core.skills.translate_skills.consistency_fix.scripts import consistency_skill
from transagent.backend.core.skills.translate_skills.chunk_translate.scripts.chunk_translate_skill import ChunkTranslateSkill
from transagent.backend.core.skills.translate_skills.consistency_fix.scripts.consistency_skill import ConsistencySkill

# ── 测试数据 ────────────────────────────────────────────────────────


def build_term_table() -> TermTable:
    tt = TermTable()
    tt.entries = [
        TermEntry(term="rolling update", translation="滚动更新",
                  domain="Kubernetes/云原生", confidence="high", action="translate"),
        TermEntry(term="Deployment", translation="Deployment",
                  domain="Kubernetes/云原生", confidence="high", action="notranslate"),
    ]
    return tt


def build_strategy() -> StrategyBook:
    return StrategyBook(
        ict_domain="Kubernetes/云原生", domain_confidence="high",
        difficulty="medium", style="technical", literal_ratio=0.6,
        target_audience="开发者",
        rules={"code": "notranslate", "tone": "professional",
               "sentence_length": "medium", "voice": "active"},
    )


def build_chunk(cid: str, text: str, order: int) -> Chunk:
    return Chunk(chunk_id=cid, source_text=text,
                 token_estimate=len(text) // 2, heading_path=[], order=order)


# 标准双chunk：各含 rolling update + 占位符
CHUNK1_SRC = ("## Rolling Update\n\n"
              "Kubernetes performs a rolling update to replace old Pods. "
              "Use {NT_0} to check status.")
CHUNK2_SRC = "The rolling update strategy ensures zero downtime. Run {NT_1} to verify the rollout."

# 一致的译文（术语用指定译法·占位符保留）
DRAFT1_OK = "## 滚动更新\n\nKubernetes 执行滚动更新，用新 Pod 替换旧 Pod。使用 {NT_0} 查看状态。"
DRAFT2_OK = "滚动更新策略确保零停机。运行 {NT_1} 验证发布。"


def build_two_chunks() -> list[Chunk]:
    return [build_chunk("chunk_001", CHUNK1_SRC, 1), build_chunk("chunk_002", CHUNK2_SRC, 2)]


# ── 内容匹配式 mock LLM（并发安全·按chunk源文特征串返回译文）──

class FakeChat:
    """按 user_message 中的特征串匹配返回译文；一致性修复按需返回修复稿。"""

    def __init__(self, cases: list[tuple[str, str]], fix: str | None = None):
        self.cases = cases            # [(marker, translation), ...]
        self.fix = fix                # 一致性修复返回（None=不允许触发）
        self.calls: list[dict] = []   # 全部主译调用记录
        self.fix_calls: list[str] = []  # 一致性修复调用记录

    async def __call__(self, system_prompt: str, user_message: str, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_message, "kwargs": kwargs})
        # D5：系统提示词 = agent说明书 + 技能说明书（追加式·skill.md），按技能特征识别
        assert ta.TRANSLATE_AGENT_SYSTEM_PROMPT in system_prompt, \
            f"调用提示词必须包含agent说明书，实际开头={system_prompt[:40]}..."
        if "翻译一致性审核专家" in system_prompt:
            self.fix_calls.append(user_message)
            if self.fix is None:
                raise AssertionError("一致性预检未通过，但测试未准备修复返回（fix=None）")
            return self.fix
        for marker, value in self.cases:
            if marker in user_message:
                return value
        raise AssertionError(f"FakeChat未匹配到case: {user_message[:100]}...")


# ── 测试用例 ────────────────────────────────────────────────────────


async def test_01_en_zh_prompt_tuning():
    print("\n[1/11] 主译Prompt调优：EN→ZH 使用调优后Prompt（few-shot·术语强制·占位符双保护）")
    tt, sb = build_term_table(), build_strategy()
    chunks = build_two_chunks()
    fake = FakeChat([("Kubernetes performs", DRAFT1_OK), ("The rolling update strategy", DRAFT2_OK)])
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    # 使用的System Prompt（skill.md双方向章节合一·以策略书direction字段路由）
    sys_p = ChunkTranslateSkill().system_prompt
    assert "## 翻译方向一：英文 → 中文" in sys_p, "EN→ZH 说明书章节应存在"
    assert "## 示例" in sys_p, "应包含few-shot示例"
    assert "术语强制使用" in sys_p and "【不译】" in sys_p, "应强调术语强制/不译"
    assert "{NT_n} / {T_n}" in sys_p, "应声明占位符双保护"
    assert "Markdown结构保留" in sys_p, "应声明Markdown结构保留"
    assert "被动句 → 主动句" in sys_p, "应声明翻译腔规避规则"

    # chunk prompt 内容
    u0 = fake.calls[0]["user"]
    assert "英文 → 中文" in u0, "方向标签应为英文→中文"
    assert "项目术语表" in u0 and "rolling update → 滚动更新" in u0, "应携带术语表"
    assert "翻译策略" in u0 and "Kubernetes/云原生" in u0, "应携带策略书"

    # 预检全通过 → 无修复调用
    assert result.consistency_report.precheck_passed, "一致译文应通过预检"
    assert result.consistency_report.issues_found == 0
    assert len(fake.calls) == 2 and not fake.fix_calls, "一致时不应触发LLM修复"
    assert "滚动更新" in result.draft and "{NT_0}" in result.draft
    print(f"  OK: 使用EN→ZH Prompt | {len(fake.calls)}次主译调用 | draft={len(result.draft)}字符")


async def test_02_zh_en_routing():
    print("\n[2/11] 方向路由：ZH→EN 使用 TRANSLATE_ZH_EN_PROMPT")
    tt, sb = build_term_table(), build_strategy()
    zh1 = build_chunk("chunk_001", "Kubernetes 执行滚动更新，用新 Pod 替换旧 Pod。使用 {NT_0} 查看状态。", 1)
    zh2 = build_chunk("chunk_002", "滚动更新策略确保零停机。运行 {NT_1} 验证发布。", 2)
    fake = FakeChat([
        ("执行滚动更新", "Kubernetes performs a rolling update to replace old Pods. Use {NT_0} to check status."),
        ("滚动更新策略", "The rolling update strategy ensures zero downtime. Run {NT_1} to verify."),
    ])
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate([zh1, zh2], tt, sb, None, direction="zh_to_en")

    sys_p = ChunkTranslateSkill().system_prompt
    assert "## 翻译方向二：中文 → 英文" in sys_p, "ZH→EN 说明书章节应存在"
    assert "## Example" in sys_p, "应包含few-shot示例"
    assert "Mandatory glossary usage" in sys_p and "notranslate" in sys_p
    assert "run-on" in sys_p, "应声明长句拆分规则"
    u0 = fake.calls[0]["user"]
    assert "中文 → 英文" in u0, "策略书direction=zh_to_en 应渲染中文→英文方向标签"
    assert result.consistency_report.precheck_passed, "一致译文应通过预检"
    assert "rolling update" in result.draft and "{NT_0}" in result.draft
    print(f"  OK: 使用ZH→EN Prompt | 预检通过 | draft含rolling update与占位符")


async def test_03_serial_context_passing():
    print("\n[3/11] 串行翻译：后一chunk携带前一chunk译文（前文翻译）")
    tt, sb = build_term_table(), build_strategy()
    chunks = build_two_chunks()
    fake = FakeChat([("Kubernetes performs", DRAFT1_OK), ("The rolling update strategy", DRAFT2_OK)])
    chunk_translate_skill.chat = consistency_skill.chat = fake

    await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    u1 = fake.calls[1]["user"]
    assert "前文翻译（上下文参考）" in u1, "第2个chunk应携带前文翻译上下文"
    assert DRAFT1_OK in u1, "前文翻译内容应传入后续chunk"
    # 前文译文不应泄漏进第1个chunk
    assert "前文翻译" not in fake.calls[0]["user"]
    print("  OK: chunk_002携带chunk_001译文（前2000字符），chunk_001无前文")


async def test_04_precheck_missing_placeholder():
    print("\n[4/11] 预检·占位符缺失（{NT_n}双保护）→ 触发LLM修复")
    tt, sb = build_term_table(), build_strategy()
    chunks = [
        build_chunk("chunk_001", "Use {NT_0} to check status. A rolling update replaces Pods.", 1),
        build_chunk("chunk_002", "Run {NT_1} to verify the rollout. Rolling updates give zero downtime.", 2),
    ]
    fake = FakeChat([
        ("Use {NT_0}", "使用该命令查看状态。滚动更新会替换 Pod。"),          # 丢了 {NT_0}
        ("Run {NT_1}", "运行 {NT_1} 验证发布。滚动更新确保零停机。"),
    ], fix="[修复] 完整译文（已恢复占位符）")
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    cr = result.consistency_report
    assert not cr.precheck_passed, "缺失占位符应判为预检失败"
    assert cr.llm_fix_triggered, "预检发现问题应触发LLM修复"
    types = {d["type"] for d in cr.details}
    assert "missing_placeholder" in types, f"应检出缺失占位符，实际={types}"
    assert len(fake.fix_calls) == 1, "应调用一次一致性修复"
    fix_u = fake.fix_calls[0]
    assert "missing_placeholder" in fix_u, "修复Prompt应携带预检问题清单"
    assert "项目术语表" in fix_u and "rolling update → 滚动更新" in fix_u, "修复Prompt应携带术语表"
    assert "英文 → 中文" in fix_u, "修复Prompt应携带方向"
    assert result.draft == "[修复] 完整译文（已恢复占位符）", "修复后译文应作为最终draft"
    print(f"  OK: issues={cr.issues_found} | details类型={sorted(types)} | 修复已触发")


async def test_05_precheck_term_untranslated():
    print("\n[5/11] 预检·术语未翻译（translate术语保留英文原文）→ 触发修复")
    tt, sb = build_term_table(), build_strategy()
    chunks = build_two_chunks()
    fake = FakeChat([
        ("Kubernetes performs", "Kubernetes 执行 rolling update，用新 Pod 替换旧 Pod。使用 {NT_0} 查看状态。"),
        ("The rolling update strategy", DRAFT2_OK),
    ], fix="[修复] 统一为滚动更新")
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    cr = result.consistency_report
    assert not cr.precheck_passed
    types = {d["type"] for d in cr.details}
    assert "term_untranslated" in types, f"应检出术语未翻译，实际={types}"
    assert "term_cross_chunk_inconsistent" in types, "跨chunk不一致也应汇总"
    assert cr.llm_fix_triggered
    assert result.draft == "[修复] 统一为滚动更新"
    print(f"  OK: issues={cr.issues_found} | 类型={sorted(types)} | 修复已触发")


async def test_06_precheck_notranslate_modified():
    print("\n[6/11] 预检·notranslate术语被改译 → 触发修复")
    tt, sb = build_term_table(), build_strategy()
    chunks = [
        build_chunk("chunk_001", "Deployment manages the rollout of new ReplicaSets. Use {NT_0}.", 1),
        build_chunk("chunk_002", "Rolling updates are the default strategy. Run {NT_1}.", 2),
    ]
    fake = FakeChat([
        ("Deployment manages", "部署 管理新 ReplicaSet 的发布。使用 {NT_0}。"),  # Deployment→部署（错）
        ("Rolling updates are", "滚动更新是默认策略。运行 {NT_1}。"),
    ], fix="[修复] Deployment 保留原文")
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    cr = result.consistency_report
    types = {d["type"] for d in cr.details}
    assert "term_notranslate_modified" in types, f"应检出notranslate被改译，实际={types}"
    assert cr.llm_fix_triggered
    assert result.draft == "[修复] Deployment 保留原文"
    print(f"  OK: issues={cr.issues_found} | 类型={sorted(types)} | 修复已触发")


async def test_07_precheck_code_block_mismatch():
    print("\n[7/11] 预检·代码块围栏数量不匹配 → 触发修复")
    tt, sb = build_term_table(), build_strategy()
    chunks = [
        build_chunk("chunk_001", "```yaml\nkind: Deployment\n```\n\nRolling updates replace Pods.", 1),
        build_chunk("chunk_002", "Run {NT_1} to verify. Rolling updates give zero downtime.", 2),
    ]
    fake = FakeChat([
        ("kind: Deployment", "```yaml\nkind: Deployment\n```\n\n滚动更新会替换 Pod。"),  # 一致
        ("Run {NT_1}", "运行 {NT_1} 验证发布。滚动更新确保零停机。"),
    ], fix="[修复] 补回代码块")
    # 注入一个丢代码块的chunk1译文
    chunk_translate_skill.chat = consistency_skill.chat = FakeChat([
        ("kind: Deployment", "滚动更新会替换 Pod。"),   # 丢了 ``` 围栏
        ("Run {NT_1}", "运行 {NT_1} 验证发布。滚动更新确保零停机。"),
    ], fix="[修复] 补回代码块")

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    cr = result.consistency_report
    types = {d["type"] for d in cr.details}
    assert "code_block_mismatch" in types, f"应检出代码块不匹配，实际={types}"
    assert cr.llm_fix_triggered
    print(f"  OK: issues={cr.issues_found} | 类型={sorted(types)} | 修复已触发")


async def test_08_all_consistent_no_llm_fix():
    print("\n[8/11] 全部一致：precheck_passed=True·零额外LLM调用")
    tt, sb = build_term_table(), build_strategy()
    chunks = build_two_chunks()
    fake = FakeChat([("Kubernetes performs", DRAFT1_OK), ("The rolling update strategy", DRAFT2_OK)])
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    cr = result.consistency_report
    assert cr.precheck_passed
    assert cr.issues_found == 0
    assert not cr.llm_fix_triggered
    assert cr.details == []
    assert len(fake.calls) == 2, "只应发生2次主译调用（无修复）"
    assert not fake.fix_calls
    assert result.draft == f"{DRAFT1_OK}\n\n{DRAFT2_OK}", "合并稿应为两份译文按序拼接"
    print(f"  OK: 2次调用·0修复·draft正确拼接({len(result.draft)}字符)")


async def test_09_parallel_translation():
    print("\n[9/11] 并行chunk翻译：顺序保留·每chunk独立携带策略+术语")
    tt, sb = build_term_table(), build_strategy()
    c3 = build_chunk("chunk_003", "StatefulSets manage stateful workloads. Rolling updates apply too. Use {NT_2}.", 3)
    chunks = build_two_chunks() + [c3]
    d3 = "StatefulSet 管理有状态工作负载。滚动更新同样适用。使用 {NT_2}。"
    fake = FakeChat([
        ("Kubernetes performs", DRAFT1_OK),
        ("The rolling update strategy", DRAFT2_OK),
        ("StatefulSets manage", d3),
    ])
    chunk_translate_skill.chat = consistency_skill.chat = fake

    result = await ta.spawn_translate_parallel(chunks, tt, sb, None, direction="en_to_zh", max_concurrency=3)

    assert len(fake.calls) == 3, f"应并行翻译3个chunk，实际={len(fake.calls)}"
    for call in fake.calls:
        u = call["user"]
        assert "翻译策略（所有chunk统一遵守）" in u, "并行chunk应携带统一策略"
        assert "术语表中的术语必须使用指定译法" in u, "并行chunk应携带跨chunk一致性要求"
        assert "并行翻译" in u, "并行chunk应提示并行模式（不依赖前文）"
    # 顺序保留
    assert result.draft.index(DRAFT1_OK) < result.draft.index(DRAFT2_OK) < result.draft.index(d3)
    assert result.consistency_report.precheck_passed, "并行一致译文应通过预检"
    print(f"  OK: 3个chunk并行翻译 | 顺序保留 | 每chunk携带统一策略+术语")


async def test_10_chunk_failure_degradation():
    print("\n[10/11] chunk翻译失败降级：失败chunk标'[翻译失败]'·不影响其余")
    tt, sb = build_term_table(), build_strategy()
    chunks = build_two_chunks()

    async def flaky_chat(system_prompt, user_message, **kwargs):
        if "The rolling update strategy" in user_message:
            raise RuntimeError("模拟LLM超时")
        return DRAFT1_OK

    chunk_translate_skill.chat = consistency_skill.chat = flaky_chat
    result = await ta.spawn_translate(chunks, tt, sb, None, direction="en_to_zh")

    assert "[翻译失败]" in result.draft, "失败chunk应标[翻译失败]"
    assert DRAFT1_OK in result.draft, "成功chunk译文应保留"
    assert "Kubernetes 执行滚动更新" in result.draft
    print(f"  OK: chunk_002失败降级，chunk_001译文保留 | draft含[翻译失败]标记")


async def test_11_prompt_quality():
    print("\n[11/11] Prompt质量：skill.md说明书包含关键约束")
    md = ChunkTranslateSkill().system_prompt          # skill.md主体（双方向章节合一）
    cs_md = ConsistencySkill().system_prompt          # consistency skill.md主体
    # EN→ZH 章节
    assert "术语强制使用" in md and "【不译】" in md
    assert "{NT_n} / {T_n}" in md
    assert "## 示例" in md and "译文：" in md
    assert "被动句 → 主动句" in md and "不要增译、漏译、曲解原文" in md
    assert "不要用代码块包裹整篇译文" in md
    # ZH→EN 章节
    assert "Mandatory glossary usage" in md and "notranslate" in md
    assert "{NT_n} and {T_n}" in md
    assert "## Example" in md and "Translation:" in md
    assert "run-on Chinese sentences" in md and "no summaries or explanations" in md
    assert "do not wrap the translation in a code block" in md
    # 一致性修复skill.md
    assert "逐条修复" in cs_md
    assert "{NT_n}/{T_n}" in cs_md
    print("  OK: skill.md含双方向章节/few-shot/术语强制/占位符双保护/Markdown/翻译腔规避")


async def main():
    print("=" * 60)
    print("  译中Sub-Agent D4 单元测试（mock LLM）")
    print("=" * 60)
    await test_01_en_zh_prompt_tuning()
    await test_02_zh_en_routing()
    await test_03_serial_context_passing()
    await test_04_precheck_missing_placeholder()
    await test_05_precheck_term_untranslated()
    await test_06_precheck_notranslate_modified()
    await test_07_precheck_code_block_mismatch()
    await test_08_all_consistent_no_llm_fix()
    await test_09_parallel_translation()
    await test_10_chunk_failure_degradation()
    await test_11_prompt_quality()
    print("\n" + "=" * 60)
    print("  全部通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
