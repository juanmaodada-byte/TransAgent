"""近义消歧表:抑制同域近义近似误报(search_rag 内部确定性护栏)。

问题(第一次 test 优化·P1):同域近义术语(如 Signature Verification ↔ Digital
Signature、GAN ↔ GenAI、Transfer Learning ↔ Supervised Learning)语义相似度可达
0.73~0.79。在词库没有正确条目的场景下会被近似命中——此时 search_rag 判定
"已命中",跳过 Web/LLM 兜底,错误译文以「RAG 命中」身份注入。置信度分级
(rag_terms._remap_confidence)已把这类命中降为 low 请求用户确认;消歧表更进一步:
对已知高混淆簇,直接抑制被排除的库内候选,使其返回空 → 调用方走 Web 搜索 /
LLM 兜底(宁可兜底,不可错译)。

存储:SQLite,与 rag_aliases.db 同库,表 disambiguation_rules:
  query_key        TEXT  查询前缀(归一化键);查询归一化键以它为前缀即触发规则
  blocked_term_key TEXT  被抑制的库内术语(归一化键)
  reason           TEXT  维护说明
匹配示例:query_key="signature" 覆盖 "signature verification" / "signature matching
  system" 等改写变体;而 "digital signature"(以 digital 开头)不受影响。

对外导出(add_rule/remove_rule/list_rules/seed_default_rules)是模块内部工具,
不进入 knowledge_base 的 6 个对外接口(接口冻结,见 开发计划.md §5)。
"""
import re
import sqlite3

from . import config

_NORM = re.compile(r"[^a-z0-9一-鿿]+")   # 与 rag_aliases._NORM 同一归一化规则


def _conn() -> sqlite3.Connection:
    config.RAG_ALIAS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.RAG_ALIAS_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS disambiguation_rules ("
        " query_key TEXT, blocked_term_key TEXT, reason TEXT,"
        " PRIMARY KEY (query_key, blocked_term_key))")
    return conn


def normalize_key(text: str) -> str:
    """术语/查询的归一化键:小写、去空白/标点,保留中英文数字(与别名层一致)。"""
    return _NORM.sub("", text.lower())


def add_rule(query_pattern: str, blocked_term: str, reason: str = "") -> None:
    """新增消歧规则:查询命中 query_pattern 前缀时,抑制库内术语 blocked_term。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO disambiguation_rules (query_key, blocked_term_key, reason)"
        " VALUES (?,?,?)"
        " ON CONFLICT(query_key, blocked_term_key) DO UPDATE SET reason=excluded.reason",
        (normalize_key(query_pattern), normalize_key(blocked_term), reason))
    conn.commit()


def remove_rule(query_pattern: str, blocked_term: str) -> None:
    """删除一条消歧规则。"""
    conn = _conn()
    conn.execute("DELETE FROM disambiguation_rules WHERE query_key=? AND blocked_term_key=?",
                 (normalize_key(query_pattern), normalize_key(blocked_term)))
    conn.commit()


def list_rules() -> list[dict]:
    """列出全部消歧规则(调试/审计用)。"""
    conn = _conn()
    return [{"query_key": r[0], "blocked_term_key": r[1], "reason": r[2]}
            for r in conn.execute(
                "SELECT query_key, blocked_term_key, reason FROM disambiguation_rules"
                " ORDER BY query_key")]


def lookup_blocked_terms(query: str) -> set[str]:
    """返回查询应抑制的库内术语(归一化键)集合;无规则时为空集。"""
    key = normalize_key(query)
    if not key:
        return set()
    conn = _conn()
    rows = conn.execute(
        "SELECT blocked_term_key FROM disambiguation_rules"
        " WHERE ? LIKE query_key || '%'", (key,)).fetchall()
    return {r[0] for r in rows}


def seed_default_rules() -> int:
    """铺默认消歧规则(基于第一次真实文档测试的高混淆簇,按「近义簇」整簇堵)。

    由 rag_terms.reset_collection 在重建内置库后自动调用;也供手工恢复默认。
    先清空再重建,保证幂等且不留历史错误键。

    注意:blocked_term 必须填**词库内真实术语名**(含括注,如 "Generative AI (GenAI)"),
    因为匹配时对库内术语做 normalize_key 后比对;漏掉括注会匹配不上(已踩过坑)。
    同域近义是结构性的(Transfer Learning 还能撞上 Reinforcement Learning),
    所以学习方法类按「簇」一次堵住所有近义候选。
    """
    rules = [
        ("signature", "Digital Signature",
         "签名类:Signature Verification / Handwritten Signature 等未命中时,勿回退 Digital Signature"),
        ("handwritten", "Digital Signature",
         "手写签名类:Handwritten Signature 未命中时,勿回退 Digital Signature"),
        ("generative adversarial", "Generative AI (GenAI)",
         "生成式AI类:Generative Adversarial Network 未命中时,勿回退 Generative AI"),
        ("transfer learning", "Supervised Learning",
         "学习类:Transfer Learning 未命中时,勿回退监督学习方法"),
        ("transfer learning", "Reinforcement Learning",
         "学习类:Transfer Learning 未命中时,勿回退强化学习方法"),
        ("transfer learning", "Unsupervised Learning",
         "学习类:Transfer Learning 未命中时,勿回退无监督学习方法"),
    ]
    conn = _conn()
    conn.execute("DELETE FROM disambiguation_rules")
    conn.executemany(
        "INSERT INTO disambiguation_rules (query_key, blocked_term_key, reason)"
        " VALUES (?,?,?)",
        [(normalize_key(q), normalize_key(b), r) for q, b, r in rules])
    conn.commit()
    return len(rules)
