"""
译前技能化·评审演示
==================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

mock LLM 演示（不产生真实API调用）——评审入口。
正式落地时，本文件由 tests/ 下的正式测试取代。

运行：
    cd "d:\\Side Projects\\Developing\\TransAgent"
    python -X utf8 -m transagent.tests.draft_demo

验证点：
  1. agent说明书含技能声明+工作流（PRE_AGENT_SYSTEM_PROMPT）
  2. 每次LLM调用都携带agent说明书（mock强制断言第一行是"译前Sub-Agent"）
  3. 工作流固定顺序：先技能一（策略）后技能二（术语）
  4. 单chunk → 2次调用；3chunk → 4次调用（技能二分批全查）
  5. 合并去重：跨批重复术语保留首次译法
"""

import asyncio
import sys
import io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import (
    UserPrefs, Chunk, PreprocessResult, PlaceholderMap,
)
from transagent.tests.draft_pre_skills import strategy_skill, term_skill   # 技能模块（mock它们的chat）
from transagent.tests import draft_pre_agent as pre_agent     # 译前agent（工作流）
from transagent.tests.draft_pre_agent import run_pre_workflow, PRE_AGENT_SYSTEM_PROMPT
from transagent.tests.draft_skill import SkillRegistry        # 技能登记处（框架模块）


# ══════════════════════════════════════════════════════════════════
# mock数据
# ══════════════════════════════════════════════════════════════════

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

# 单chunk场景：完整一批（含重复术语·空术语·普通词·pending）
FAKE_TERMS_FULL = {
    "term_table": [
        {"term": "rolling update", "translation": "滚动更新", "confidence": "high",
         "action": "translate", "source": "LLM生成"},
        {"term": "Deployment", "translation": "Deployment", "confidence": "high",
         "action": "notranslate", "source": "LLM生成"},
        {"term": "readiness probe", "translation": "就绪探针", "confidence": "medium",
         "action": "translate", "source": "LLM生成"},
        {"term": "rolling update", "translation": "滚动更新", "confidence": "high",   # 批内重复
         "action": "translate", "source": "LLM生成"},
        {"term": "", "translation": "", "confidence": "medium",                        # 空术语
         "action": "translate", "source": ""},
        {"term": "network", "translation": "网络", "confidence": "medium",             # 普通词
         "action": "translate", "source": "LLM生成"},
    ],
    "pending_terms": [
        {"term": "GitOps pipeline", "translation": "GitOps流水线", "confidence": "low",
         "action": "translate", "source": "LLM生成"},
    ],
}

# 多chunk场景（3个chunk·模拟分批返回）
FAKE_TERMS_PART1 = {
    "term_table": [
        {"term": "rolling update", "translation": "滚动更新", "confidence": "high",
         "action": "translate", "source": "LLM生成"},
        {"term": "Deployment", "translation": "Deployment", "confidence": "high",
         "action": "notranslate", "source": "LLM生成"},
    ],
    "pending_terms": [],
}
FAKE_TERMS_PART2 = {
    "term_table": [
        # 跨批重复·译法不同 → 合并时首次（批次1的"滚动更新"）优先
        {"term": "rolling update", "translation": "滚动更新（批次2版本）", "confidence": "high",
         "action": "translate", "source": "LLM生成"},
        {"term": "node affinity", "translation": "节点亲和", "confidence": "high",
         "action": "translate", "source": "LLM生成"},
    ],
    "pending_terms": [],
}
FAKE_TERMS_PART3 = {
    "term_table": [
        {"term": "Helm", "translation": "Helm", "confidence": "high",
         "action": "notranslate", "source": "LLM生成"},
    ],
    "pending_terms": [
        {"term": "GitOps pipeline", "translation": "GitOps流水线", "confidence": "low",
         "action": "translate", "source": "LLM生成"},
    ],
}

CALLS: list[dict] = []   # 记录每次LLM调用


def make_mock_chat():
    """模拟LLM：按技能说明书特征区分。策略说明书含"翻译策略专家"，术语说明书含"术语专家"。

    同时验证：系统提示词以 agent说明书 开头（每次调用都带着译前agent身份+工作流）。
    """
    async def mock_chat(system_prompt, user_message, **kwargs):
        agent_header = system_prompt.strip().splitlines()[0]
        assert "译前Sub-Agent" in agent_header, \
            f"调用提示词必须包含agent说明书，实际开头={agent_header[:40]}"
        # 从完整提示词中定位技能说明书的第一行（"你正在执行【技能X：...】"）
        skill_line = next(
            (ln.strip() for ln in system_prompt.splitlines() if ln.strip().startswith("你正在执行【技能")),
            system_prompt[:40],
        )
        CALLS.append({
            "kind": "技能一·策略" if "翻译策略专家" in system_prompt else "技能二·术语",
            "skill": skill_line[:44],
            "user": user_message.replace("\n", " ")[:80],
        })
        if "术语专家" in system_prompt:
            # 多chunk场景：按"本次处理文本：第X/Y部分"区分返回
            if "第1/3部分" in user_message:
                return FAKE_TERMS_PART1
            if "第2/3部分" in user_message:
                return FAKE_TERMS_PART2
            if "第3/3部分" in user_message:
                return FAKE_TERMS_PART3
            return FAKE_TERMS_FULL
        return FAKE_STRATEGY
    return mock_chat


def build_preprocess(num_chunks: int = 1) -> PreprocessResult:
    """构造预处理产物（模拟主agent已完成的 结构解析+分块）"""
    chunk_texts = {
        1: ["## Rolling Update\n\nA rolling update allows zero downtime. "
            "Use kubectl to monitor progress."],
        3: [
            "## Rolling Update\n\nA rolling update allows zero downtime.",
            "## Node Affinity\n\nNode affinity is a scheduling rule.",
            "## Helm\n\nHelm is a package manager for Kubernetes.",
        ],
    }
    texts = chunk_texts[num_chunks]
    chunks = [
        Chunk(chunk_id=f"chunk_{i:03d}", source_text=t, token_estimate=len(t) // 3,
              heading_path=[t.splitlines()[0]], order=i)
        for i, t in enumerate(texts)
    ]
    return PreprocessResult(
        protected_md="\n\n".join(texts),
        chunks=chunks,
        placeholder_map=PlaceholderMap(nt_count=0, t_count=0),
        token_estimate_total=sum(c.token_estimate for c in chunks),
        chunk_count=len(chunks),
    )


def _print_calls(label: str) -> None:
    print(f"  -- 调用记录（{label}·每次调用都携带agent说明书）--")
    for i, c in enumerate(CALLS):
        print(f"   #{i + 1} [{c['kind']}] {c['skill']}... | 用户消息: {c['user'][:68]}...")


async def demo_01_single_chunk() -> None:
    """演示1：单chunk文档 → 2次LLM调用（技能一1次 + 技能二1次·读全文）"""
    print("=" * 64)
    print("演示1：单chunk文档（最常见·90%+场景）")
    print("=" * 64)
    strategy_skill.chat = term_skill.chat = make_mock_chat()   # mock两个技能模块的chat
    CALLS.clear()

    pre = build_preprocess(num_chunks=1)
    sb, tt = await run_pre_workflow(pre, UserPrefs(user_id="u1"))

    _print_calls("单chunk")
    assert len(CALLS) == 2, f"单chunk应2次调用，实际{len(CALLS)}次"
    assert sb.ict_domain == "Kubernetes/云原生", sb
    terms = {e.term: e for e in tt.entries}
    assert "rolling update" in terms
    assert "readiness probe" in terms
    assert len(tt.entries) == len(set(e.term for e in tt.entries)), "批内重复未去重"
    assert all(e.term for e in tt.entries), "空术语未被过滤"
    pendings = {e.term for e in tt.pending_entries}
    assert "GitOps pipeline" in pendings
    print(f"\n  ✅ 2次调用（按工作流：技能一→技能二）| 策略={sb.ict_domain} | "
          f"术语{len(tt.entries)}条+pending{len(tt.pending_entries)}条 | 去重/空过滤通过")


async def demo_02_multi_chunk() -> None:
    """演示2：多chunk文档（3个chunk）→ 4次LLM调用（技能一1 + 技能二3·分批全查）+ 合并去重"""
    print("\n" + "=" * 64)
    print("演示2：多chunk文档（3个chunk·分批全查）")
    print("=" * 64)
    strategy_skill.chat = term_skill.chat = make_mock_chat()
    CALLS.clear()

    pre = build_preprocess(num_chunks=3)
    sb, tt = await run_pre_workflow(pre, UserPrefs(user_id="u1"))

    _print_calls("多chunk")
    assert len(CALLS) == 4, f"3chunk应4次调用（技能一1+技能二3），实际{len(CALLS)}次"

    terms = {e.term: e for e in tt.entries}
    # 三个chunk的术语都覆盖到（跨批全查）
    assert "node affinity" in terms, "批次2的术语应被提取"
    assert "Helm" in terms, "批次3的术语应被提取"
    # 跨批去重·首次译法优先（批次2的"滚动更新（批次2版本）"应被丢弃）
    assert terms["rolling update"].translation == "滚动更新", \
        f"首次译法应优先，实际={terms['rolling update'].translation}"
    # 无重复
    assert len(tt.entries) == len(set(e.term for e in tt.entries))
    pendings = {e.term for e in tt.pending_entries}
    assert "GitOps pipeline" in pendings
    assert tt.total_count == len(tt.entries) + len(tt.pending_entries)

    print(f"\n  ✅ 4次调用（技能一1 + 技能二3）| 术语{len(tt.entries)}条+pending{len(tt.pending_entries)}条")
    print(f"  ✅ 跨批全查：批次2/3独有术语均被提取")
    print(f"  ✅ 跨批去重：rolling update 保留批次1译法「滚动更新」（非批次2版本）")


async def main():
    print("=" * 64)
    print("  译前技能化·评审演示（mock LLM）")
    print(f"  已注册技能: {SkillRegistry.list_all()}")
    print(f"  agent说明书含技能声明+工作流: "
          f"{'技能一：策略制定' in PRE_AGENT_SYSTEM_PROMPT and '技能二：术语提取' in PRE_AGENT_SYSTEM_PROMPT}")
    print("=" * 64)
    await demo_01_single_chunk()
    await demo_02_multi_chunk()
    print("\n" + "=" * 64)
    print("  评审演示运行通过 ✅")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
