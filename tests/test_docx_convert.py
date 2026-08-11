"""
DOCX 转换测试
==============
D1 测试基线 — DOCX→MD 转换的结构保真度验证。

不依赖 API key、网络或真实 LLM。
"""
import os
import pytest
from docx import Document
from TransAgent.interface import FormatType


class TestDocxConversion:
    """DOCX 转换基础测试"""

    def test_convert_produces_content(self, cloud_native_docx_path):
        """DOCX 转换产生非空内容"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert len(result.md_text) > 100, "DOCX conversion should produce substantial output"

    def test_convert_preserves_text(self, cloud_native_docx_path):
        """DOCX 转换保留关键文本"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        # 关键文本应出现在 MD 中
        assert "云原生应用部署指南" in result.md_text
        assert "kubectl" in result.md_text
        assert "部署" in result.md_text


# ══════════════════════════════════════════════════════════════════
# DOCX 顺序与结构 — 已知缺陷 (XFAIL)
# ══════════════════════════════════════════════════════════════════

class TestDocxOrderAndStructure:
    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: DOCX parser appends tables after all paragraphs, losing source order"
    )
    def test_docx_element_order_preserved(self, cloud_native_docx_path):
        """DOCX 元素顺序: heading → paragraph → list → heading → table → paragraph"""
        doc = Document(cloud_native_docx_path)

        # 解析源 DOCX 的原始元素顺序
        source_elements = []
        for el in doc.element.body:
            tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag
            if tag == 'p':
                # 判断段落类型
                p_el = el.find('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pStyle')
                if p_el is not None and 'Heading' in (p_el.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val') or ''):
                    source_elements.append('heading')
                else:
                    source_elements.append('paragraph')
            elif tag == 'tbl':
                source_elements.append('table')

        # 从转换后的 MD 文本推断顺序
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        md_lines = result.md_text.split('\n')

        md_sequence = []
        for line in md_lines:
            stripped = line.strip()
            if stripped.startswith('#'):
                md_sequence.append('heading')
            elif stripped.startswith('|') and '|' in stripped[1:]:
                if 'heading' not in [md_sequence[-1]] if md_sequence else True:
                    md_sequence.append('table')
            elif stripped == '':
                continue
            else:
                if not md_sequence or md_sequence[-1] != 'paragraph':
                    md_sequence.append('paragraph')

        # 期望标题在表格前面（源文档中 ## 2. 资源配置表 在表格前）
        heading_idx = None
        table_idx = None
        for i, el in enumerate(md_sequence):
            if el == 'heading' and '资源配置' in md_lines[i]:
                heading_idx = i
            if el == 'table' and heading_idx is not None and table_idx is None:
                table_idx = i

        assert heading_idx is not None, "Could not find table heading in MD"
        assert table_idx is not None, "Could not find table in MD"
        assert heading_idx < table_idx, (
            f"Heading #{heading_idx} should appear before table #{table_idx}"
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: DOCX parser 先读所有段落再追加所有表格；source 中表格在第2节后第3节前，MD 输出中表格在末尾"
    )
    def test_docx_table_cells_preserved(self, cloud_native_docx_path):
        """表格应出现在后续章节之前（source 中表格在"3. 部署清单示例"之前）"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)

        md_text = result.md_text
        # "100m" 仅在表格中出现（CPU 请求列），不出现于任何段落/标题/代码块
        table_pos = md_text.find("100m")
        after_heading = md_text.find("3. 部署清单示例")

        assert table_pos != -1, "Table cell value '100m' not found in MD"
        assert after_heading != -1, "Section '3. 部署清单示例' not found in MD"

        # P0 期望: 表格在 "3. 部署清单示例" 之前（源文档顺序）
        # 当前行为: 所有表格在最后，表格在 "3. 部署清单示例" 之后
        assert table_pos < after_heading, (
            f"Table should appear BEFORE '3. 部署清单示例' (source order). "
            f"Got table_pos={table_pos}, heading_pos={after_heading}"
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: 图片提取后没有在 MD 中插入位置引用"
    )
    def test_docx_image_in_md_with_ref(self, cloud_native_docx_path):
        """图片在转换后的 MD 中有位置引用"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        # P0 期望: MD 中包含 ![...](assets/...) 引用
        assert '![' in result.md_text, "No image reference found in MD output"
        assert 'img_' in result.md_text, "No image path reference in MD output"

    def test_docx_image_extracted_count(self, cloud_native_docx_path):
        """图片提取数量与源文件一致"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert result.image_count == 1, (
            f"Expected 1 image, got {result.image_count}"
        )

    def test_docx_conversion_warnings_structure(self, cloud_native_docx_path):
        """ConvertResult 支持 conversion_warnings（在 PreprocessResult 中）"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        # ConvertResult.metadata 至少包含转换器名称
        assert result.metadata is not None
        assert "converter" in result.metadata
