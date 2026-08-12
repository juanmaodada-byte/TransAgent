"""缩写/别名解析:确定性精确匹配,先于语义检索。

问题:官方词库术语常带括注,如 "Central Processing Unit (CPU)"。源文里常见
裸缩写 "CPU",bge-m3 对"缩写↔全称"短词只给 0.6~0.7 相似度,过不了 0.70 阈值。
对策(对应概念文档 §4.1 白名单思想):导入时从术语提取别名,查询先做一次
确定性精确匹配——零成本、100% 准确,不依赖语义阈值。

别名提取规则(对含 "(...)" 的术语):
  "Central Processing Unit (CPU)" → 别名 {CPU, central processing unit}
  "BIOS (Basic Input/Output System)" → 别名 {BIOS, basic input/output system}
无括注术语仅自身作为别名。别名键统一小写、去空白/标点,便于大小写混合匹配。
"""
import re
import sqlite3

from . import config
from .glossary_split import split_variants

_PATTERN = re.compile(r"^(?P<leading>.+?)\s*\((?P<inner>[^()]+)\)\s*$")
_NORM = re.compile(r"[^a-z0-9一-鿿]+")   # 保留字母/数字/中文,其余去掉


def _conn() -> sqlite3.Connection:
    config.RAG_ALIAS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.RAG_ALIAS_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS term_aliases ("
        " alias_key TEXT, user_id TEXT, term TEXT, domain TEXT,"
        " PRIMARY KEY (alias_key, user_id))")
    return conn


def _normalize(text: str) -> str:
    return _NORM.sub("", text.lower())


def extract_aliases(term: str) -> list[str]:
    """从术语提取规范化别名键列表(去重保序)。

    term 含空格斜杠(直接 write_rag 的合并词条)时,对 leading 生成子形式键,
    使 TCP/IP 类合并词条即使绕过 import_glossary 也能被子形式命中。
    无空格斜杠术语(CPU、I/O、TCP/IP)产出与原实现逐键相同。
    """
    s = term.strip()
    m = _PATTERN.match(s)
    candidates: list[str] = []
    if m:
        lead, inner = m.group("leading"), m.group("inner")
        lead_parts = split_variants(lead)          # "Transmission Control Protocol / Internet Protocol" → 2 段
        candidates.extend(lead_parts)
        candidates.append(inner)                   # 整体缩写 "TCP/IP" → tcpip
        inner_parts = [p.strip() for p in inner.split("/") if p.strip()]
        if len(inner_parts) == len(lead_parts) and len(inner_parts) > 1:
            for i, lp in enumerate(lead_parts):    # 位置配对:TCP↔Transmission Control Protocol
                candidates.append(f"{lp} ({inner_parts[i]})")
                candidates.append(inner_parts[i])
    else:
        candidates.extend(split_variants(s))       # 无括注的 "Handover / Handoff" 直接拆
    candidates.append(s)
    keys = [_normalize(c) for c in candidates]
    return list(dict.fromkeys(k for k in keys if k))


_ZH = re.compile(r"[一-鿿]")


def _zh_keys(translation: str) -> list[str]:
    """从中文译法提取别名键(仅中文含义,2026-08-10 中文源文支持)。

    动机:产品支持中文源文文档时,术语提取拿中文候选(如"数字签名")查 RAG。
    中文 query 与英文 term 的跨语言语义相似度不足(实测"云计算"→Cloud
    Computing 仅 0.69<0.70,过不了阈值),复用缩写别名机制做确定性命中:
    中文译法 → 英文术语,一对一无歧义。

    只取中文部分(中英混合译法如"数据挖掘(Data Mining)"取"数据挖掘"),
    避免从译文引入英文键去撞已存在的英文术语别名。

    译文含空格斜杠(如 "分组交换 / 包交换")时,每段各自归一化生成独立中文键,
    修复单一合并键覆盖不到子译名的问题;无空格斜杠(输入/输出)保持整体。
    """
    s = (translation or "").strip()
    if not s or not _ZH.search(s):
        return []
    keys: list[str] = []
    for piece in split_variants(s):          # 空格斜杠拆段;无空格斜杠保持整体
        m = _PATTERN.match(piece)
        lead = m.group("leading").strip() if m else piece
        keys.extend(k for k in extract_aliases(lead) if _ZH.search(k))
    return list(dict.fromkeys(keys))


def update_aliases(entries, user_id: str) -> None:
    """write_rag 后调用:为写入的术语重建别名行(upsert)。

    除英文术语自身的别名(全称/括注缩写),额外把中文译法也建为别名键,
    支持中文源文文档:中文候选 query 经此确定性命中英文术语。
    """
    if not entries:
        return
    conn = _conn()
    rows = []
    for e in entries:
        for k in extract_aliases(e.term):
            rows.append((k, user_id, e.term, e.domain))
        for k in _zh_keys(e.translation):
            rows.append((k, user_id, e.term, e.domain))
    conn.executemany(
        "INSERT INTO term_aliases (alias_key, user_id, term, domain)"
        " VALUES (?,?,?,?)"
        " ON CONFLICT(alias_key, user_id) DO UPDATE SET"
        " term=excluded.term, domain=excluded.domain",
        rows)
    conn.commit()


def clear_aliases(user_id: str | None = None) -> None:
    """清空别名表(重建内置库时调用,防旧别名悬空)。"""
    conn = _conn()
    if user_id:
        conn.execute("DELETE FROM term_aliases WHERE user_id=?", (user_id,))
    else:
        conn.execute("DELETE FROM term_aliases")
    conn.commit()


def lookup_alias(query: str, domain: str, user_id: str) -> tuple[str, str, str] | None:
    """别名确定性查询。命中返回 (term, domain, owner_user_id),否则 None。

    领域语义与 _build_where 一致:查询带领域时,只命中「同领域」或「无领域(全局)」的别名。
    """
    key = _normalize(query)
    if not key:
        return None
    conn = _conn()
    if user_id:
        rows = conn.execute(
            "SELECT term, domain, user_id FROM term_aliases WHERE alias_key=? AND user_id=?",
            (key, user_id)).fetchall()
    else:
        # 调用方未指定用户:匹配任意用户(与语义检索"无 user 过滤"行为一致)
        rows = conn.execute(
            "SELECT term, domain, user_id FROM term_aliases WHERE alias_key=?", (key,)).fetchall()
    for term, d, owner in rows:
        if not domain or d == domain or d == "":
            return term, d, owner
    return None
