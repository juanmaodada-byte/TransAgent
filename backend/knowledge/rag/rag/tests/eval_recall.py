"""RAG 术语库召回率测试(见 知识库设计.md §10)。

用法:
    python tests/eval_recall.py                      # 全量测试(默认 eval_queries.json)
    python tests/eval_recall.py --file tests/eval_queries_official.json  # 指定测试集
    python tests/eval_recall.py --domain kubernetes  # 只看某领域

输出:
  - 每条查询的 PASS/FAIL 与 top1 详情
  - 整体指标:Recall@1 / Recall@5 / MRR / 命中判定准确率
  - 阈值敏感性(0.60~0.85):帮助校准 config.RAG_MIN_SIMILARITY
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge_base import config, init_collection, search_rag   # noqa: E402
from knowledge_base.embedder import embed                        # noqa: E402
from knowledge_base.rag_terms import _build_where, _collection, _parse_results  # noqa: E402

EVAL_QUERIES = Path(__file__).resolve().parent / "eval_queries.json"
TOP_K = 5


def load_queries(path: Path | None = None) -> list[dict]:
    p = path or EVAL_QUERIES
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def raw_query(query: str, domain: str) -> list[dict]:
    """全量余弦检索(不过阈值),返回按相似度降序的 [{term, translation, _similarity}]。"""
    col = _collection()
    if col.count() == 0:
        return []
    res = col.query(query_embeddings=[embed(query)],
                    where=_build_where(domain, config.DEFAULT_USER),
                    n_results=col.count())
    return _parse_results(res)


def evaluate(queries: list[dict]) -> None:
    n = len(queries)
    hit_qs = [q for q in queries if q.get("expected")]
    n_hit = len(hit_qs)

    r1 = r5 = mrr_sum = judge_ok = 0
    fail_rows = []
    hit_sims: list[float] = []     # 每条命中查询:期望术语的原始相似度
    miss_max_sims: list[float] = []  # 每条未命中查询:最近术语的相似度(判误报)

    print("=" * 80)
    print(f"RAG 召回率测试 | 查询 {n} 条(命中 {n_hit} / 未命中 {n - n_hit}) | "
          f"当前阈值 {config.RAG_MIN_SIMILARITY}")
    print("=" * 80)

    for q in queries:
        query, domain = q["query"], q.get("domain", "")
        expected, note = q.get("expected", ""), q.get("note", "")
        expect_hit = bool(expected)

        t0 = time.time()
        raw = raw_query(query, domain)
        results = search_rag(query, domain=domain, top_k=TOP_K)
        dt = time.time() - t0

        # 期望术语在「过阈值后 search_rag 实际返回」中的位置(1-based;0=未返回)
        found = [r.term for r in results]
        rank = found.index(expected) + 1 if expected in found else 0

        # 期望术语的原始相似度(不过阈值,供阈值敏感性分析)
        exp_sim = 0.0
        for e in raw:
            if e.get("term") == expected:
                exp_sim = float(e.get("_similarity", 0.0))
                break
        top1 = results[0] if results else None
        top1_raw = raw[0] if raw else None
        top1_sim = float(top1_raw.get("_similarity", 0.0)) if top1_raw else 0.0

        decided_hit = len(results) > 0
        ok = (decided_hit == expect_hit)
        if ok:
            judge_ok += 1

        if expect_hit:
            hit_sims.append(exp_sim)
            if rank == 1:
                r1 += 1
                r5 += 1
                mrr_sum += 1.0
            elif rank > 0:
                r5 += 1
                mrr_sum += 1.0 / rank
        else:
            miss_max_sims.append(top1_sim)

        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {query!r:<40} rank={rank or '-':<2} "
              f"top1={top1.term if top1 else '∅'}(sim {top1_sim:.2f}) "
              f"| {note} | {dt*1000:.0f}ms")
        if not ok:
            fail_rows.append((query, domain, expected, top1.term if top1 else "", exp_sim))

    # ── 汇总指标 ────────────────────────────────────────────
    recall1 = r1 / n_hit * 100 if n_hit else 0.0
    recall5 = r5 / n_hit * 100 if n_hit else 0.0
    mrr = mrr_sum / n_hit if n_hit else 0.0
    judge_acc = judge_ok / n * 100 if n else 0.0

    print("=" * 80)
    print(f"Recall@1 = {r1}/{n_hit} = {recall1:.1f}%")
    print(f"Recall@5 = {r5}/{n_hit} = {recall5:.1f}%")
    print(f"MRR      = {mrr:.3f}")
    print(f"命中判定准确率 = {judge_ok}/{n} = {judge_acc:.1f}%")

    # ── 阈值敏感性 ──────────────────────────────────────────
    print("-" * 80)
    print("阈值敏感性(0.60~0.85):")
    for t in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]:
        keep = sum(1 for s in hit_sims if s >= t)
        fp = sum(1 for s in miss_max_sims if s >= t)
        print(f"  阈值 ≥ {t:.2f}: 命中保留 {keep}/{n_hit} "
              f"(召回率 {keep/n_hit*100 if n_hit else 0:.0f}%)  |  负例误判 {fp}/{n-n_hit}")
    if hit_sims:
        srt = sorted(hit_sims)
        print(f"  命中查询相似度: min={srt[0]:.2f} max={srt[-1]:.2f} median={srt[n_hit//2]:.2f}")
        print(f"  建议阈值:至少低于最低命中相似度,且能压住负例 top1 相似度(取一平衡点)")

    if fail_rows:
        print("-" * 80)
        print("未通过项:")
        for query, domain, expected, top1, sim in fail_rows:
            print(f"  {query!r}({domain}): 期望 {expected!r} / 实际 top1 {top1!r} / "
                  f"期望相似度 {sim:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 术语库召回率测试")
    parser.add_argument("--domain", default="", help="只看某领域(如 kubernetes)")
    parser.add_argument("--file", default="", help="测试集 JSON 路径(默认 tests/eval_queries.json)")
    args = parser.parse_args()

    init_collection()
    qs = load_queries(Path(args.file) if args.file else None)
    if args.domain:
        qs = [q for q in qs if q.get("domain") == args.domain]
        print(f"过滤领域 {args.domain}:剩余 {len(qs)} 条")
    evaluate(qs)
