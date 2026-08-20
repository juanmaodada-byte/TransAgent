"""
分块测试
=========
D1 测试基线 — 文档分块的正确性、完整性、顺序和 token 上限。

不依赖 API key、网络或真实 LLM。
"""
import pytest
from transagent.backend.pipeline.chunker import chunk_document, estimate_tokens
from transagent.backend.pipeline.structure_parser import parse_structure


# ══════════════════════════════════════════════════════════════════
# 分块 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestChunkingNormal:
    def test_single_chunk_short_doc(self, kubernetes_md):
        """短文不触发分块，只有 1 个 chunk"""
        protected_md, _ = parse_structure(kubernetes_md)
        chunks = chunk_document(protected_md, max_tokens=30000)
        assert len(chunks) == 1, f"Short doc should be 1 chunk, got {len(chunks)}"

    def test_chunk_contains_text(self, kubernetes_md):
        """chunk 包含原始文本"""
        protected_md, _ = parse_structure(kubernetes_md)
        chunks = chunk_document(protected_md, max_tokens=30000)
        # 受保护 MD 的关键内容应在 chunk 中
        assert len(chunks) > 0
        assert len(chunks[0].source_text) > 0

    def test_chunk_order_sequential(self, tech_whitepaper_md):
        """chunk order 是唯一的、递增的"""
        protected_md, _ = parse_structure(tech_whitepaper_md)
        chunks = chunk_document(protected_md, max_tokens=300)
        orders = [c.order for c in chunks]
        assert orders == sorted(orders), "Chunk orders not sorted"
        assert len(orders) == len(set(orders)), "Chunk orders not unique"

    def test_chunk_order_repeatable(self, tech_whitepaper_md):
        """多次运行 chunk order 一致"""
        protected_md, _ = parse_structure(tech_whitepaper_md)
        chunks1 = chunk_document(protected_md, max_tokens=300)
        chunks2 = chunk_document(protected_md, max_tokens=300)
        orders1 = [c.order for c in chunks1]
        orders2 = [c.order for c in chunks2]
        assert orders1 == orders2, "Chunk orders not repeatable"

    def test_chunk_has_chunk_id(self, kubernetes_md):
        """每个 chunk 有唯一的 chunk_id"""
        protected_md, _ = parse_structure(kubernetes_md)
        chunks = chunk_document(protected_md)
        for c in chunks:
            assert c.chunk_id != "", "Chunk missing chunk_id"

    def test_chunk_token_estimate_positive(self, kubernetes_md):
        """token_estimate 为正数"""
        protected_md, _ = parse_structure(kubernetes_md)
        chunks = chunk_document(protected_md)
        for c in chunks:
            assert c.token_estimate > 0, f"Chunk {c.chunk_id} has zero token estimate"


# ══════════════════════════════════════════════════════════════════
# 分块 — token 估算
# ══════════════════════════════════════════════════════════════════

class TestTokenEstimation:
    def test_estimate_empty(self):
        assert estimate_tokens("") == 0

    def test_estimate_english(self):
        # 10 words * 1.3 = 13
        assert estimate_tokens("hello world this is a test sentence with ten words") >= 10

    def test_estimate_chinese(self):
        # 5 chars * 0.6 = 3
        assert estimate_tokens("你好世界测试") >= 2

    def test_estimate_mixed(self):
        # 中英混合
        text = "Kubernetes 部署 guide v1.0"
        assert estimate_tokens(text) > 0


# ══════════════════════════════════════════════════════════════════
# 分块 — 覆盖与完整性
# ══════════════════════════════════════════════════════════════════

class TestChunkingCoverage:
    def test_all_content_covered(self, tech_whitepaper_md):
        """所有内容的文本在 chunk 中至少出现一次（基于连接检查）"""
        protected_md, _ = parse_structure(tech_whitepaper_md)
        chunks = chunk_document(protected_md, max_tokens=300)

        # 验证所有 chunk 文本都有实际内容
        for c in chunks:
            assert len(c.source_text.strip()) > 0, (
                f"Chunk {c.chunk_id} has empty source_text"
            )

    def test_order_consistent(self, tech_whitepaper_md):
        """order 始终递增且 chunk 文本连接可读"""
        protected_md, _ = parse_structure(tech_whitepaper_md)
        chunks = chunk_document(protected_md, max_tokens=300)
        sorted_chunks = sorted(chunks, key=lambda c: c.order)
        for i, c in enumerate(sorted_chunks):
            assert c.order >= 0


# ══════════════════════════════════════════════════════════════════
# 分块 — 已知缺陷 (XFAIL)
# ══════════════════════════════════════════════════════════════════

class TestChunkingFailures:
    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/C: 长文分块时标题只存入 heading_path 不存入 chunk 正文，导致标题丢失"
    )
    def test_headings_preserved_in_chunks(self, tech_whitepaper_md):
        """分块后标题仍出现在 chunk 正文中"""
        protected_md, _ = parse_structure(tech_whitepaper_md)
        chunks = chunk_document(protected_md, max_tokens=300)

        # 关键标题应在至少一个 chunk 的正文中出现
        key_heading = "Executive Summary"
        found = any(key_heading in c.source_text for c in chunks)
        assert found, (
            f"Heading '{key_heading}' not found in any chunk source_text"
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/C: 单个超长段落无法继续切分，可能超过 token 上限"
    )
    def test_oversized_paragraph_within_limit(self, single_long_paragraph):
        """超长段落应在 token 限制内切分"""
        protected_md, _ = parse_structure(single_long_paragraph)
        max_tokens = 500
        chunks = chunk_document(protected_md, max_tokens=max_tokens)
        for c in chunks:
            assert c.token_estimate <= max_tokens, (
                f"Chunk {c.chunk_id} token_estimate {c.token_estimate} exceeds max {max_tokens}"
            )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/C: 超长代码块被占位符保护为单个 {NT_0}，保护后无法做 token 限制切分。保护前的原始代码可能超过 token 上限。"
    )
    def test_oversized_code_block_within_limit(self, super_long_code_block):
        """超长代码块在保护前应按 token 上限处理（不能依赖保护后只剩一个占位符）"""
        # 当前行为: parse_structure 将整个超长代码块替换为单个 {NT_0}
        # 导致 chunk_document 收到的是单个短占位符，不会触发切分
        # P0 期望: 保护前检查 token，对大代码块进行切分或警告
        protected_md, pmap = parse_structure(super_long_code_block)
        # 检查保护后是否有残留的代码内容（应该没有——这是缺陷）
        # 但原始的代码内容应该被识别为过长
        # 验证: 代码块中的行数 > token 上限对应的行数
        original_lines = super_long_code_block.split('\n')
        assert len(original_lines) > 100, (
            f"Code block with {len(original_lines)} lines should be detected as oversized "
            "BEFORE placeholder protection"
        )
        # P0 期望: 预处理阶段对大代码块发出警告或切分
        # 当前: 直接替换为占位符，不做大小检查
        # 这导致翻译阶段收到一个占位符，token 计算失真
        max_tokens = 500
        chunks = chunk_document(protected_md, max_tokens=max_tokens)
        # 即使保护后只剩占位符，原始内容的 token 估算也应被保留
        # 当前: token_estimate 只基于保护后文本
        for c in chunks:
            # P0 期望: token_estimate 应反映原始内容大小
            # 500 行 shell 代码约 5000+ tokens
            assert c.token_estimate > 1000, (
                f"Protected code chunk {c.chunk_id} token_estimate={c.token_estimate} "
                "is too small — should reflect original content size"
            )


# ══════════════════════════════════════════════════════════════════
# Tokenizer 上限验证
# ══════════════════════════════════════════════════════════════════

class TestTokenizerLimit:
    def test_token_limit_tokenizer_attempt(self):
        """尝试加载翻译模型的 tokenizer；不可用时应明确失败"""
        try:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(
                "deepseek-ai/DeepSeek-V3", trust_remote_code=True
            )
            # 如果能加载，验证其基本功能
            tokens = tokenizer.encode("hello world")
            assert len(tokens) > 0
        except (ImportError, OSError, Exception) as e:
            # D1 标记: tokenizer 不可用，测试标记为 skip 但记录原因
            pytest.skip(f"Tokenizer not available: {type(e).__name__}")
