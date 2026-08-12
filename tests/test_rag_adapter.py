"""
知识库适配层单测(方案A·整合 2026-08-11)
==========================================
验证三个适配器(rag_terms/tm_store/user_prefs)对外的**旧签名**不变、内部正确
委托成员 C 新交付包,且契约统一转回 transagent.interface 类型。

策略:
  - search_rag / write_rag / get_term_count 需 bge-m3 或真实数据 → 用 mock 委托,
    断言关键字参数映射正确(规避新旧参数顺序错位)+ 领域归一化 + 用户映射。
  - search_tm / write_tm / prefs / 计数 → 走新交付包真实 SQLite(不依赖模型)。

运行:
    cd "d:/Side Projects/Developing/TransAgent"
    python -X utf8 -m transagent.tests.test_rag_adapter
"""
import gc
import io
import os
import shutil
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.interface import TermEntry as ITermEntry, TMEntry as ITMEntry, UserPrefs
from transagent.backend.knowledge import rag_terms as rag_adapter
from transagent.backend.knowledge import tm_store as tm_adapter
from transagent.backend.knowledge import user_prefs as prefs_adapter
from transagent.backend.knowledge import _backend
from transagent.backend.knowledge import _domain_map

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def _fake_contract_term(term="rolling update", translation="滚动更新",
                        domain="kubernetes", confidence="high", action="translate",
                        source="RAG命中"):
    """构造新交付包 contracts.TermEntry 形态对象(用简单命名空间模拟,不引真实 contracts)。"""
    from types import SimpleNamespace
    return SimpleNamespace(term=term, translation=translation, domain=domain,
                           confidence=confidence, action=action, source=source)


# ── 1. search_rag ───────────────────────────────────────────────

def test_search_rag_delegates_with_correct_mapping():
    print("\n[1] search_rag 委托映射(旧签名 → 新包关键字)")
    captured = {}

    def fake_search_rag(query="", domain="", user_id="", top_k=5):
        captured.update(query=query, domain=domain, user_id=user_id, top_k=top_k)
        return [_fake_contract_term()]

    with mock.patch.object(_backend.rag_terms, "search_rag", fake_search_rag):
        hits = rag_adapter.search_rag("rolling update", "demo_user", "Kubernetes/云原生")

    check("query 透传", captured.get("query") == "rolling update", captured)
    check("领域归一化(Kubernetes/云原生→kubernetes)",
          captured.get("domain") == "kubernetes", captured)
    check("用户映射(demo_user→default)", captured.get("user_id") == "default", captured)
    check("top_k 默认 5", captured.get("top_k") == 5, captured)
    check("返回 interface.TermEntry 且补 user_id",
          len(hits) == 1 and isinstance(hits[0], ITermEntry) and hits[0].user_id == "default",
          [type(h) for h in hits])
    check("命中的库中存储词条随返回(缩写场景)",
          hits[0].term == "rolling update" and hits[0].translation == "滚动更新")


def test_search_rag_empty_term_no_delegate():
    print("\n[2] search_rag 空术语不触发委托")
    called = []

    def fake_search_rag(*a, **kw):
        called.append(1)
        return []

    with mock.patch.object(_backend.rag_terms, "search_rag", fake_search_rag):
        hits = rag_adapter.search_rag("  ", "u1", "数据库")
    check("空术语返回 [] 且零调用", hits == [] and not called, called)


def test_search_rag_unmapped_domain_falls_back_empty():
    print("\n[3] search_rag 未映射领域回退 ''(命中全局通用术语)")
    captured = {}

    def fake_search_rag(query="", domain="", user_id="", top_k=5):
        captured["domain"] = domain
        return []

    with mock.patch.object(_backend.rag_terms, "search_rag", fake_search_rag):
        rag_adapter.search_rag("foo", "u1", "编程语言")
    check("未映射领域 → ''", captured.get("domain") == "", captured)


# ── 2. write_rag_terms / get_term_count ─────────────────────────

def test_write_rag_terms_normalizes_and_delegates():
    print("\n[4] write_rag_terms 归一化领域 + 委托新包")
    captured = {}

    def fake_write_rag(entries, user_id=""):
        captured["user_id"] = user_id
        captured["domains"] = [e.domain for e in entries]
        captured["terms"] = [e.term for e in entries]
        return ["id1", "id2"]

    entries = [
        ITermEntry(term="rolling update", translation="滚动更新", domain="Kubernetes/云原生",
                   user_id="demo_user", confidence="high"),
        ITermEntry(term="kubectl", translation="kubectl", domain="Kubernetes/云原生",
                   action="notranslate", user_id="demo_user", confidence="high"),
    ]
    with mock.patch.object(_backend.rag_terms, "write_rag", fake_write_rag):
        n = rag_adapter.write_rag_terms(entries)

    check("返回写入条数 2", n == 2, n)
    check("用户映射 demo_user→default", captured.get("user_id") == "default", captured)
    check("领域归一化写入", captured.get("domains") == ["kubernetes", "kubernetes"], captured)
    check("term 透传", captured.get("terms") == ["rolling update", "kubectl"], captured)


def test_write_rag_terms_failure_returns_zero():
    print("\n[5] write_rag_terms 失败降级返回 0(不抛异常)")

    def boom(*a, **kw):
        raise RuntimeError("model missing")

    with mock.patch.object(_backend.rag_terms, "write_rag", boom):
        n = rag_adapter.write_rag_terms([ITermEntry(term="x", translation="y", user_id="u1")])
    check("失败返回 0 不中断", n == 0, n)


def test_get_term_count():
    print("\n[6] get_term_count 委托 ChromaDB 过滤计数")

    class FakeCol:
        def count(self):
            return 10

        def get(self, where=None):
            return {"ids": ["a", "b", "c"]}

    with mock.patch.object(_backend.rag_terms, "_collection", lambda: FakeCol()):
        n = rag_adapter.get_term_count("demo_user")
    check("计数 = 过滤后 ids 数", n == 3, n)


# ── 3. TM(真实 SQLite,不依赖模型)───────────────────────────────

def test_tm_search_and_write_real_sqlite():
    print("\n[7] TM 检索/写入(真实新交付包 SQLite)")
    tmp_dir = tempfile.mkdtemp()
    try:
        with mock.patch.object(_backend.kb_config, "TM_DB_PATH", Path(tmp_dir) / "tm.db"):
            # 写一对
            n = tm_adapter.write_tm_entries([
                ITMEntry(source_seg="Run the following command:",
                         target_seg="运行以下命令：", quality_score=9.2, user_id="demo_user"),
            ])
            check("write_tm_entries 写入 1 条", n == 1, n)
            # 精确检索
            hits = tm_adapter.search_tm("Run the following command:", "demo_user")
            check("search_tm 命中 1 条且 similarity>=0.85",
                  len(hits) == 1 and hits[0].similarity >= 0.85 and hits[0].target_seg == "运行以下命令：",
                  [(h.source_seg, round(h.similarity, 2)) for h in hits])
            check("返回 interface.TMEntry 且补 user_id=default",
                  isinstance(hits[0], ITMEntry) and hits[0].user_id == "default", hits[0])
            # 计数
            check("get_tm_count = 1", tm_adapter.get_tm_count("demo_user") == 1,
                  tm_adapter.get_tm_count("demo_user"))
    finally:
        # 新交付包 SQLite 连接未显式关闭(GC 释放),Windows 下需先 gc 才能删临时目录
        gc.collect()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 4. 用户偏好(真实 SQLite)─────────────────────────────────────

def test_prefs_roundtrip_real_sqlite():
    print("\n[8] 用户偏好往返(真实新交付包 SQLite)")
    tmp_dir = tempfile.mkdtemp()
    try:
        with mock.patch.object(_backend.kb_config, "TM_DB_PATH", Path(tmp_dir) / "tm.db"):
            prefs_adapter.save_user_prefs(UserPrefs(
                user_id="demo_user", default_style="tutorial",
                literal_ratio=0.5, term_preferences={"pod": "Pod"},
                domain_tags=["kubernetes"],
            ))
            loaded = prefs_adapter.load_user_prefs("demo_user")
            check("default_style 往返", loaded.default_style == "tutorial", loaded.default_style)
            check("literal_ratio 往返", abs(loaded.literal_ratio - 0.5) < 1e-6, loaded.literal_ratio)
            check("term_preferences 往返", loaded.term_preferences == {"pod": "Pod"}, loaded.term_preferences)
            check("domain_tags 往返", loaded.domain_tags == ["kubernetes"], loaded.domain_tags)
    finally:
        gc.collect()
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 5. 领域归一化 ───────────────────────────────────────────────

def test_domain_map():
    print("\n[9] 领域归一化映射表")
    check("Kubernetes/云原生 → kubernetes",
          _domain_map.normalize_domain("Kubernetes/云原生") == "kubernetes")
    check("数据科学/ML → data_ml", _domain_map.normalize_domain("数据科学/ML") == "data_ml")
    check("未映射 → ''", _domain_map.normalize_domain("IoT") == "")
    check("空 → ''", _domain_map.normalize_domain("") == "")
    check("None → ''", _domain_map.normalize_domain(None) == "")
    # 幂等:已是封闭词表值原样透传(写入时不被清空)
    check("已是封闭词表 network 透传", _domain_map.normalize_domain("network") == "network")
    check("大写 Kubernetes 归一", _domain_map.normalize_domain("Kubernetes") == "kubernetes")


if __name__ == "__main__":
    test_search_rag_delegates_with_correct_mapping()
    test_search_rag_empty_term_no_delegate()
    test_search_rag_unmapped_domain_falls_back_empty()
    test_write_rag_terms_normalizes_and_delegates()
    test_write_rag_terms_failure_returns_zero()
    test_get_term_count()
    test_tm_search_and_write_real_sqlite()
    test_prefs_roundtrip_real_sqlite()
    test_domain_map()
    print(f"\n========== 适配层测试: {PASS} 通过 / {FAIL} 失败 ==========")
    sys.exit(1 if FAIL else 0)
