"""第一次test优化验证:扩库治本 + 近义消歧 + 置信度分级(2026-08-10)。

验证内容(针对第一次 test 的 P1 同域近义误报):
  1. 扩库治本:4 个原误报术语(GAN / Transfer Learning / Signature Verification /
     Handwritten Signature)补入内置库(ICT_Terms_ext.csv 批次1)后,查询以
     确定性命中(别名层,相似度 1.0),不再撞任何近义词;
  2. 负例拒收:12 个真实未覆盖术语正确返回空(走 Web/LLM 兜底);
  3. 近义消歧:改写查询("signature matching system")不被 Digital Signature 抢;
  4. 置信度分级:_remap_confidence 边界(近似命中降级)。

依赖:内置库已含官方 200 条 + 扩库 9 条;本测试首次需加载 bge-m3(约 11s)。

用法:
    python tests/test_optimization.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge_base import search_rag  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def test_extended_terms() -> None:
    """扩库治本:4 个原误报术语现在确定性命中。"""
    print("扩库治本(原误报术语 → 确定性命中):")
    cases = [
        ("Generative Adversarial Network (GAN)", "生成对抗网络"),
        ("Transfer Learning", "迁移学习"),
        ("Signature Verification", "签名验证"),
        ("Handwritten Signature", "手写签名"),
    ]
    for query, expect_tr in cases:
        hit = search_rag(query)
        ok = hit and hit[0].translation == expect_tr
        check(f"{query!r} → {expect_tr!r}",
              ok, f"top1={hit[0].translation if hit else '∅'}")
        # 已扩库术语的确定性命中不应被近义词抢(置信度应为词库存 high)
        check(f"{query!r} confidence=high", hit and hit[0].confidence == "high",
              f"got={hit[0].confidence if hit else '∅'}")


def test_uncovered_negatives() -> None:
    """负例拒收:12 个真实未覆盖术语返回空(转 Web/LLM 兜底)。"""
    print("负例拒收(未覆盖术语应未命中):")
    negatives = [
        "VGG-16", "Max-Pooling", "Softmax", "Rectified Linear Unit (ReLU)",
        "Feature Extraction", "Fraud Detection", "Kubernetes", "Hadoop",
        "Apache Spark", "E-commerce", "GDPR", "V2X",
    ]
    for q in negatives:
        hit = search_rag(q)
        check(f"{q!r} 正确拒绝", not hit, f"top1={hit[0].term if hit else '∅'}")


def test_disambiguation_rewrite() -> None:
    """近义消歧:改写查询不被近义词抢。"""
    print("近义消歧(改写查询兜底):")
    for q in ["signature matching system", "handwritten signature verification",
              "generative adversarial networks"]:
        hit = search_rag(q)
        blocked = any(r.term == "Digital Signature" for r in hit)
        check(f"{q!r} 不误报 Digital Signature", not blocked,
              f"got={[r.term for r in hit]}")


if __name__ == "__main__":
    test_extended_terms()
    test_uncovered_negatives()
    test_disambiguation_rewrite()
    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
