"""学习层回写闭环验证(A 端集成 write_rag 的契约测试)。

目的(第一次 test 优化·P0 学习层回写):证明「译后确认术语 → write_rag →
下次 search_rag 立即确定性命中」链路可用。A 端(orchestrator)在译后按本测试的
用法调用 write_rag(source="用户确认")即可完成学习层闭环,无需新接口
(接口契约见 开发计划.md §3 / 知识库设计.md §4)。

同时覆盖本次优化的另外两块:
  - 置信度分级:rag_terms._remap_confidence 的单元断言;
  - 近义消歧:写入 Signature Verification 后,查询不再被 Digital Signature 抢。

用法:
    python tests/test_learning_loop.py   (首次加载 bge-m3 约 11s,随后 batch 秒级)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import TermEntry                             # noqa: E402
from knowledge_base import search_rag, write_rag            # noqa: E402
from knowledge_base.rag_terms import _remap_confidence     # noqa: E402

PASS = FAIL = 0
USER = "learning_loop_user"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_write_then_search() -> None:
    """学习层闭环:write_rag 写入 → search_rag 立即命中。"""
    print("学习层回写闭环:")
    ids = write_rag([
        TermEntry(term="Signature Verification (SV)", translation="签名验证",
                  domain="security", confidence="high", action="translate",
                  source="用户确认"),
    ], user_id=USER)
    check("write_rag 返回 ids", len(ids) == 1, f"got {len(ids)}")

    # 全称 → 别名层确定性命中
    hit = search_rag("Signature Verification", domain="security", user_id=USER)
    check("写后立即命中(全称)", hit and hit[0].term == "Signature Verification (SV)",
          f"top1={hit[0].term if hit else '∅'}")
    check("返回用户确认的译文", hit and hit[0].translation == "签名验证",
          f"got={hit[0].translation if hit else ''}")
    check("source 保持用户确认", hit and hit[0].source == "用户确认",
          f"got={hit[0].source if hit else ''}")

    # 裸缩写 → 别名层(括注自动建别名)
    hit2 = search_rag("SV", domain="security", user_id=USER)
    check("缩写别名确定性命中", hit2 and hit2[0].term == "Signature Verification (SV)",
          f"top1={hit2[0].term if hit2 else '∅'}")

    # 近义消歧联动:已入库后,查询不再被 Digital Signature 抢(1.0 命中自己)
    hit3 = search_rag("Signature Verification", user_id=USER)
    check("消歧联动:不再回退 Digital Signature",
          hit3 and hit3[0].term == "Signature Verification (SV)",
          f"top1={hit3[0].term if hit3 else '∅'}")

    # 覆盖更新:同 term 新译文 → upsert 生效
    write_rag([TermEntry(term="Signature Verification (SV)", translation="签名核对",
                         domain="security", confidence="high", action="translate",
                         source="用户确认")], user_id=USER)
    hit4 = search_rag("Signature Verification", user_id=USER)
    check("重复确认覆盖新译文(upsert)", hit4 and hit4[0].translation == "签名核对",
          f"got={hit4[0].translation if hit4 else ''}")


def test_confidence_remap() -> None:
    """置信度分级:近似命中不再冒充高置信。"""
    print("置信度分级(_remap_confidence):")
    check("相似度 0.75 → low", _remap_confidence("high", 0.75) == "low",
          f"got={_remap_confidence('high', 0.75)}")
    check("相似度 0.85 → medium", _remap_confidence("high", 0.85) == "medium",
          f"got={_remap_confidence('high', 0.85)}")
    check("相似度 0.99 → high", _remap_confidence("high", 0.99) == "high",
          f"got={_remap_confidence('high', 0.99)}")
    check("词库存 low 不被抬高", _remap_confidence("low", 0.99) == "low",
          f"got={_remap_confidence('low', 0.99)}")
    check("默认 medium 的 0.72 近似 → low", _remap_confidence("medium", 0.72) == "low",
          f"got={_remap_confidence('medium', 0.72)}")


def test_disambiguation() -> None:
    """近义消歧表:已知高混淆簇的近似候选被抑制。"""
    print("近义消歧表:")
    from knowledge_base.rag_disambiguation import add_rule, lookup_blocked_terms, remove_rule
    add_rule("testpattern", "unrelated term", "测试规则")   # 用独立键,避免误删默认种子规则
    blocked = lookup_blocked_terms("testpattern alpha")
    check("改写查询触发规则(前缀匹配)", "unrelatedterm" in blocked, f"got={blocked}")
    blocked2 = lookup_blocked_terms("another beta")
    check("不匹配前缀的查询不触发", len(blocked2) == 0, f"got={blocked2}")
    remove_rule("testpattern", "unrelated term")
    check("删除规则后不再抑制", "unrelatedterm" not in lookup_blocked_terms("testpattern alpha"))


if __name__ == "__main__":
    test_write_then_search()
    test_confidence_remap()
    test_disambiguation()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
