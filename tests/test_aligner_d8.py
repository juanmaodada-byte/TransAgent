"""
D8 句对齐回归测试
=================
修复"句对齐错行"：译文合并/拆分句子时，顺序对齐整段错位。
本套件锁定：
  1. 2:1 合并（两个源句 → 一个译句）：译文挂在块首源句行，不错位
  2. 1:2 拆分（一个源句 → 两个译句）：译句拼在一行
  3. 行主键=源句：build_triple_alignment 源/初译/终译三栏稳定对照
  4. locate_quote 仍能按句对定位
  5. 标题对齐不被破坏
"""
import pytest

from transagent.interface import AlignedPair
from transagent.backend.pipeline.aligner import (
    align_sentences,
    build_triple_alignment,
    locate_quote,
)


def _rows(pairs):
    return [(p.source_seg, p.target_seg) for p in pairs]


def test_2to1_merge_no_drift():
    """译文把两个源句合并成一句 → 合并译文应挂第1源句行，第2源句行为空译。"""
    src = """The API gateway is the entry point for client requests.
It validates the token before forwarding to the backend service.
The system uses JSON Web Tokens for authentication."""
    draft = """API网关是客户端请求的入口点，它验证令牌，然后转发到后端服务。
系统使用JSON Web令牌进行身份验证。"""

    pairs = align_sentences(src, draft)
    rows = _rows(pairs)

    assert len(rows) == 3, f"行主键=源句，应3行，实际={len(rows)}"
    # 第1源句 → 合并译文（含"入口点"与"转发"两个源句的内容）
    assert "入口点" in rows[0][1] and "转发" in rows[0][1]
    assert rows[0][0].startswith("The API gateway")
    # 第2源句行无独立译文（已并入上一行）——不得出现错位译文
    assert rows[1][1] == ""
    assert rows[1][0].startswith("It validates")
    # 第3源句 → 自己的译文
    assert "系统" in rows[2][1]
    assert rows[2][0].startswith("The system")


def test_1to2_split_kept_inline():
    """一个源句拆成两个译句 → 两译句拼在源句行内。"""
    src = "Deploy the gateway to production and ensure TLS is enabled on all endpoints."
    draft = "将网关部署到生产环境。确保所有端点都启用TLS。"
    pairs = align_sentences(src, draft)
    rows = _rows(pairs)
    assert len(rows) == 1
    assert "部署到生产环境" in rows[0][1] and "启用TLS" in rows[0][1]


def test_heading_alignment_kept():
    """标题句对不被内容句"抢走"（顺序错位回归：Heading↔标题）。"""
    src = ("## Heading\n\n"
           "Kubernetes uses containers to package applications. "
           "A rolling update allows zero downtime.")
    draft = "## 标题\n\nKubernetes 使用容器来打包应用。滚动更新允许零停机。"
    pairs = align_sentences(src, draft)
    rows = _rows(pairs)
    assert rows[0][0] == "## Heading"
    assert rows[0][1] == "## 标题"
    assert "Kubernetes 使用容器来打包应用。" in rows[1][1]
    assert "滚动更新允许零停机。" in rows[2][1]


def test_triple_alignment_draft_keyed():
    """三栏对齐：源句为行主键，终译跟随初译（D9.2：终译是初译的润色·同语言更可靠）。

    初译合并 src[1,2] 时，终译若按源句粒度拆分，会跨行错位（源↔终译对齐受
    润色句子结构变化影响会漂）。改为终译对齐初译：终译挂在对应初译行上。
    """
    src = """The API gateway is the entry point for client requests.
It validates the token before forwarding to the backend service.
The system uses JSON Web Tokens for authentication."""
    draft = """API网关是客户端请求的入口点，它验证令牌，然后转发到后端服务。
系统使用JSON Web令牌进行身份验证。"""
    final = """API网关是客户端请求的入口点。
它在转发到后端服务之前验证令牌。
系统使用JSON Web令牌进行身份验证。"""

    rows = build_triple_alignment(src, draft, final)
    assert [r["source_seg"] for r in rows] == [
        "The API gateway is the entry point for client requests.",
        "It validates the token before forwarding to the backend service.",
        "The system uses JSON Web Tokens for authentication.",
    ], "源句为行主键·顺序保持"
    # 初译列：合并译文在第1源句行，第2源句行留空（不串行）
    assert rows[0]["draft_seg"] and "转发" in rows[0]["draft_seg"]
    assert rows[1]["draft_seg"] == ""
    assert "系统" in rows[2]["draft_seg"]
    # 终译列：跟随初译——终译[1]+[2]合并挂到初译第1行，不跨行错位
    assert "入口点" in rows[0]["final_seg"]
    assert "验证令牌" in rows[0]["final_seg"]  # 终译第2句并入初译第1行（不串行）
    assert rows[1]["final_seg"] == ""
    assert "系统" in rows[2]["final_seg"]


def test_locate_quote_still_works():
    """QA摘抄句定位：源/译摘抄命中同一句对。"""
    src = """Kubernetes uses containers to package applications.
A rolling update allows zero downtime."""
    draft = """Kubernetes 使用容器来打包应用。
滚动更新允许零停机。"""
    pairs = align_sentences(src, draft)
    pair, idx = locate_quote(
        pairs,
        "Kubernetes uses containers to package applications.",
        "Kubernetes 使用容器来打包应用。",
    )
    assert pair is not None and idx == 0
    assert "container" in pair.source_seg

    # 无关句子不命中
    none_pair, none_idx = locate_quote(pairs, "The weather is nice today.", "今天天气不错。")
    assert none_pair is None and none_idx == -1


def test_empty_target_side():
    """译文为空 → 每源句一行·target 全空（不抛异常）。"""
    pairs = align_sentences("One sentence here. Another sentence there.", "")
    assert len(pairs) == 2
    assert all(p.target_seg == "" for p in pairs)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
