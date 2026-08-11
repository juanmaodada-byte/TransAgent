"""
契约与消费者测试
================
D1 契约测试：覆盖 Dataclass 默认值独立性、关键字构造、旧调用兼容和序列化。

不依赖 API key、网络或真实 LLM。
"""
import pytest
from TransAgent.interface import (
    DocumentBlock, DocumentAsset, TranslatedBlock,
    PreprocessResult, Chunk, PlaceholderMap, FormatResult, FormatType,
)


# ══════════════════════════════════════════════════════════════════
# DocumentBlock
# ══════════════════════════════════════════════════════════════════

class TestDocumentBlock:
    def test_defaults_independent(self):
        b1 = DocumentBlock()
        b2 = DocumentBlock()
        assert b1.metadata is not b2.metadata, "metadata default 共享可变值"

    def test_keyword_construction(self):
        b = DocumentBlock(
            block_id="b1", block_type="heading", source_text="## Title",
            order=1, metadata={"level": 2}
        )
        assert b.block_id == "b1"
        assert b.block_type == "heading"
        assert b.source_text == "## Title"
        assert b.order == 1
        assert b.metadata == {"level": 2}

    def test_default_block_id_empty(self):
        b = DocumentBlock()
        assert b.block_id == ""

    def test_metadata_default_empty_dict(self):
        b = DocumentBlock()
        assert b.metadata == {}
        assert isinstance(b.metadata, dict)


# ══════════════════════════════════════════════════════════════════
# DocumentAsset
# ══════════════════════════════════════════════════════════════════

class TestDocumentAsset:
    def test_keyword_construction(self):
        a = DocumentAsset(
            asset_id="a1", path="assets/img_01.png",
            media_type="image/png", source_block_id="b5"
        )
        assert a.asset_id == "a1"
        assert a.path == "assets/img_01.png"
        assert a.media_type == "image/png"
        assert a.source_block_id == "b5"

    def test_defaults_empty(self):
        a = DocumentAsset()
        assert a.asset_id == ""
        assert a.path == ""
        assert a.media_type == ""
        assert a.source_block_id == ""


# ══════════════════════════════════════════════════════════════════
# TranslatedBlock
# ══════════════════════════════════════════════════════════════════

class TestTranslatedBlock:
    def test_keyword_construction(self):
        t = TranslatedBlock(block_id="b1", target_text="译文", status="translated")
        assert t.block_id == "b1"
        assert t.target_text == "译文"
        assert t.status == "translated"


# ══════════════════════════════════════════════════════════════════
# PreprocessResult — 新增字段 & 向后兼容
# ══════════════════════════════════════════════════════════════════

class TestPreprocessResultContract:
    def test_new_fields_present_with_defaults(self):
        """D1 新增字段存在且有默认值"""
        p = PreprocessResult(protected_md="test")
        assert p.blocks == []
        assert p.assets_dir == ""
        assert p.assets == []
        assert p.conversion_warnings == []
        assert p.schema_version == "2.0"

    def test_defaults_independent_across_instances(self):
        """共享可变默认值隔离"""
        p1 = PreprocessResult(protected_md="test")
        p2 = PreprocessResult(protected_md="test")
        assert p1.blocks is not p2.blocks
        assert p1.assets is not p2.assets
        assert p1.conversion_warnings is not p2.conversion_warnings

    def test_keyword_construction_all_fields(self):
        chunk = Chunk(chunk_id="c1", source_text="hello")
        block = DocumentBlock(block_id="b1", block_type="paragraph", source_text="hello", order=0)
        asset = DocumentAsset(asset_id="a1", path="img.png", media_type="image/png", source_block_id="b1")
        pmap = PlaceholderMap()

        p = PreprocessResult(
            protected_md="# Title\n\nhello",
            chunks=[chunk],
            placeholder_map=pmap,
            token_estimate_total=100,
            chunk_count=1,
            blocks=[block],
            assets_dir="/tmp/assets",
            assets=[asset],
            conversion_warnings=["unsupported element: chart"],
            schema_version="2.0",
        )
        assert len(p.chunks) == 1
        assert len(p.blocks) == 1
        assert len(p.assets) == 1
        assert len(p.conversion_warnings) == 1
        assert p.assets_dir == "/tmp/assets"
        assert p.schema_version == "2.0"

    def test_old_caller_still_works(self):
        """D1 扩展前的调用方式仍可工作"""
        chunk = Chunk(chunk_id="c1", source_text="hello")
        pmap = PlaceholderMap()
        p = PreprocessResult(
            protected_md="# Title",
            chunks=[chunk],
            placeholder_map=pmap,
            token_estimate_total=50,
            chunk_count=1,
        )
        assert p.protected_md == "# Title"
        assert len(p.chunks) == 1
        assert p.schema_version == "2.0"
        assert p.blocks == []
        assert p.assets_dir == ""

    def test_blocks_field_accepts_document_blocks(self):
        b = DocumentBlock(block_id="b1", block_type="heading", source_text="# H1", order=0)
        p = PreprocessResult(protected_md="test", blocks=[b])
        assert len(p.blocks) == 1
        assert p.blocks[0].block_type == "heading"


# ══════════════════════════════════════════════════════════════════
# Chunk — 新增字段
# ══════════════════════════════════════════════════════════════════

class TestChunkContract:
    def test_new_fields_present_with_defaults(self):
        c = Chunk(chunk_id="c1", source_text="text")
        assert c.block_ids == []
        assert c.context_block_ids == []
        assert c.token_count == 0

    def test_defaults_independent(self):
        c1 = Chunk(chunk_id="c1", source_text="t")
        c2 = Chunk(chunk_id="c2", source_text="t")
        assert c1.block_ids is not c2.block_ids
        assert c1.context_block_ids is not c2.context_block_ids

    def test_old_caller_still_works(self):
        """D1 前使用关键字构造的方式仍兼容"""
        c = Chunk(
            chunk_id="chunk_1",
            source_text="hello world",
            token_estimate=5,
            heading_path=["## Title"],
            order=0,
        )
        assert c.chunk_id == "chunk_1"
        assert c.source_text == "hello world"
        assert c.token_estimate == 5
        assert c.heading_path == ["## Title"]
        assert c.order == 0
        # 新增字段取默认值
        assert c.block_ids == []
        assert c.context_block_ids == []
        assert c.token_count == 0


# ══════════════════════════════════════════════════════════════════
# PlaceholderMap — 行为回归（未改动，但契约测试需要）
# ══════════════════════════════════════════════════════════════════

class TestPlaceholderMap:
    def test_empty_defaults(self):
        pm = PlaceholderMap()
        assert pm.nt_map == {}
        assert pm.t_map == {}
        assert pm.nt_count == 0
        assert pm.t_count == 0

    def test_to_dict(self):
        pm = PlaceholderMap(nt_count=2, t_count=1)
        d = pm.to_dict()
        assert d["nt_count"] == 2
        assert d["t_count"] == 1
