"""缝合怪词条拆分:一条词条用空格包围的 `/` 缝合多个独立概念时,拆成独立词条。

共享实现(唯一拆分入口):import_glossary.ingest_glossary 导入时拆分(治本);
tests/eval_recall_article.py / tests/eval_recall_book.py 生成测试集时用同一拆分,
保证测试 expected 与库内词条名一致。

触发条件只看 term 层是否有空格包围的 `/`:
  "Transmission Control Protocol / Internet Protocol (TCP/IP)"  → 拆
  "Handover / Handoff"                                          → 拆(英美同义词)
  "Input/Output (I/O)" / "BIOS (...)" / "TCP/IP" / "CI/CD"      → 不拆(无空格斜杠)
  译文层斜杠同义词("分组交换 / 包交换")不触发词条拆分,由 rag_aliases 规则层处理。
"""
import re

# 仅空格包围的 `/` 才拆(Handover / Handoff);I/O、TCP/IP、传输控制协议/网际协议 保持整体
_SLASH_SPLIT = re.compile(r"\s*/\s+|\s+/\s*")
# 末尾括号(与 rag_aliases._PATTERN 同一形态)
_PAREN_TAIL = re.compile(r"^(?P<lead>.+?)\s*\((?P<inner>[^()]+)\)\s*$")


def split_variants(p: str) -> list[str]:
    """仅空格包围的 `/` 才拆分;`I/O`、`TCP/IP` 保持整体。"""
    return [v.strip() for v in _SLASH_SPLIT.split(p) if v.strip()]


def split_combined_term(term: str, translation: str) -> list[tuple[str, str]]:
    """拆分缝合怪词条 → [(term, translation), ...]。非缝合怪原样返回 [(term, translation)]。

    以 TCP/IP 为例:
      "Transmission Control Protocol / Internet Protocol (TCP/IP)" / "传输控制协议/网际协议"
        → ("Transmission Control Protocol (TCP)", "传输控制协议")
        → ("Internet Protocol (IP)",           "网际协议")
        → ("TCP/IP",                           "TCP/IP")   # ICT 惯例协议缩写不译
      "Handover / Handoff" / "切换"
        → ("Handover", "切换")
        → ("Handoff",  "切换")
    """
    t = (term or "").strip()
    tr = (translation or "").strip()
    parts = split_variants(t)
    if len(parts) <= 1:                 # 无空格斜杠 → 非缝合怪
        return [(t, tr)]

    # 末尾括号:缩写本身可能含无空格斜杠(TCP/IP),拆出后与 leading 部分位置配对
    m = _PAREN_TAIL.match(parts[-1])
    inner_pieces: list[str] = []
    if m:
        parts[-1] = m.group("lead").strip()
        inner_pieces = [p.strip() for p in m.group("inner").split("/") if p.strip()]

    # 译文按 `/` 无条件拆分,与 leading 部分位置配对;数量不匹配则整条译文共享
    tr_parts = [p.strip() for p in tr.split("/") if p.strip()]
    per_tr = tr_parts if len(tr_parts) == len(parts) else [tr] * len(parts)

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(t_: str, tr_: str) -> None:
        t_ = t_.strip()
        if not t_ or t_ in seen:
            return
        seen.add(t_)
        out.append((t_, tr_.strip()))

    for i, part in enumerate(parts):
        new_term = part
        if m and len(inner_pieces) == len(parts):       # 逐片回贴括号缩写
            new_term = f"{part} ({inner_pieces[i]})"
        add(new_term, per_tr[i])

    # 组合缩写保留为独立词条(书内 "TCP/IP" 表面形式需命中;主译最长匹配 TCP/IP 先于 TCP)
    if m and len(inner_pieces) > 1:
        add("/".join(inner_pieces), "/".join(inner_pieces))
    return out
