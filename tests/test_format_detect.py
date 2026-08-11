"""
格式检测与输入测试
===================
D1 测试基线 — MD/TXT/DOCX 识别、未知格式拒绝、损坏文件处理。

不依赖 API key、网络或真实 LLM。
"""
import os
import pytest
from tests.conftest import FIXTURES_DIR
from TransAgent.interface import FormatType


# ══════════════════════════════════════════════════════════════════
# 格式检测 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestFormatDetection:
    def test_detect_md(self, kubernetes_md, tmp_path):
        """MD 文件被正确识别为 MARKDOWN"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.format_type == FormatType.MARKDOWN.value

    def test_detect_txt(self, tmp_path):
        """TXT 文件被正确识别"""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.format_type == FormatType.TEXT.value

    def test_detect_docx(self, cloud_native_docx_path):
        """DOCX 文件被正确识别"""
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(cloud_native_docx_path)
        assert result.format_type == FormatType.DOCX.value

    def test_detect_returns_format_result(self, kubernetes_md, tmp_path):
        """detect_format 返回 FormatResult，含 size_bytes"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.size_bytes > 0
        assert result.metadata is not None

    def test_detect_nonexistent_file(self):
        """不存在的文件应抛出 FileNotFoundError"""
        from TransAgent.backend.pipeline.preprocess import detect_format
        with pytest.raises(FileNotFoundError):
            detect_format("/nonexistent/path/file.txt")


# ══════════════════════════════════════════════════════════════════
# 格式检测 — 失败用例
# ══════════════════════════════════════════════════════════════════

class TestFormatDetectionFailures:
    def test_detect_unsupported_format(self, unsupported_ext_file):
        """未支持格式: P0 应明确拒绝 .xyz，当前默认 TEXT"""
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(unsupported_ext_file)
        # P0 期望: 未知格式应被拒绝（抛出异常），当前行为是 fallback 到 TEXT
        # 注: 此测试断言当前行为(PASS)，不是期望行为
        # 期望行为应在阶段 B 改为 raise ValueError
        assert result.format_type == FormatType.TEXT.value

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: .doc 被映射为 DOCX 而非拒绝或报错；ext_map 将 .doc 设为 FormatType.DOCX"
    )
    def test_detect_doc_not_docx(self, dot_doc_file):
        """.doc 不应被识别为 DOCX"""
        from TransAgent.backend.pipeline.preprocess import detect_format
        result = detect_format(dot_doc_file)
        # P0 期望: .doc 应该被拒绝（不是 DOCX）
        assert result.format_type != FormatType.DOCX.value, (
            ".doc file detected as DOCX — old format not supported at P0"
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: 损坏 DOCX 应返回稳定错误码 DOCUMENT_INTEGRITY_ERROR，当前抛出未包装的 BadZipFile 或其包装异常缺少结构化错误码"
    )
    def test_corrupt_docx_fails_cleanly(self, corrupt_docx_file):
        """损坏 DOCX 应产生包含稳定错误码的明确错误"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        try:
            result = convert_to_md(corrupt_docx_file, FormatType.DOCX.value)
            assert False, "Corrupt DOCX did not raise an error"
        except Exception as e:
            # P0 期望: 异常消息包含 DOCUMENT_INTEGRITY_ERROR 错误码
            msg = str(e)
            assert "DOCUMENT_INTEGRITY_ERROR" in msg, (
                f"Corrupt file error should contain DOCUMENT_INTEGRITY_ERROR code. "
                f"Got: {type(e).__name__}: {msg[:200]}"
            )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: 非法编码文件应返回编码相关的稳定错误码，当前抛出 UnicodeDecodeError 或类似基础异常"
    )
    def test_illegal_encoding_fails_cleanly(self, illegal_txt_encoding):
        """非法编码文件应产生包含编码提示和错误码的结构化错误"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        try:
            result = convert_to_md(illegal_txt_encoding, FormatType.TEXT.value)
            assert False, "Illegal encoding TXT did not raise an error"
        except Exception as e:
            msg = str(e)
            # P0 期望: 同时包含编码相关提示和 DOCUMENT_INTEGRITY_ERROR
            has_encoding = any(w in msg.lower() for w in ["encoding", "decode", "utf", "charset", "编码"])
            has_error_code = "DOCUMENT_INTEGRITY_ERROR" in msg
            assert has_encoding and has_error_code, (
                f"Error should mention encoding AND contain DOCUMENT_INTEGRITY_ERROR. "
                f"Got: {type(e).__name__}: {msg[:200]}"
            )


# ══════════════════════════════════════════════════════════════════
# 输入转换 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestInputConversion:
    def test_convert_md_passthrough(self, kubernetes_md, tmp_path):
        """MD 输入直接通过，内容不变"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        result = convert_to_md(str(f), FormatType.MARKDOWN.value)
        assert result.md_text == kubernetes_md
        assert result.image_count == 0

    def test_convert_txt_passthrough(self, tmp_path):
        """TXT 输入直接通过"""
        f = tmp_path / "test.txt"
        f.write_text("plain text", encoding="utf-8")
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        result = convert_to_md(str(f), FormatType.TEXT.value)
        assert result.md_text == "plain text"

    def test_convert_docx_produces_text(self, cloud_native_docx_path):
        """DOCX 转换产生非空 MD 文本"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert len(result.md_text) > 0, "DOCX conversion produced empty text"
        assert result.image_count >= 1, "Expected at least 1 image from fixture"

    def test_convert_docx_has_assets_dir(self, cloud_native_docx_path):
        """DOCX 转换产生 assets_dir"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert result.assets_dir != "", "DOCX conversion should produce assets_dir"

    def test_convert_docx_returns_convert_result(self, cloud_native_docx_path):
        """convert_to_md 返回 ConvertResult"""
        from TransAgent.backend.pipeline.preprocess import convert_to_md
        from TransAgent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        # ConvertResult 至少要有 md_text
        assert hasattr(result, 'md_text')
        assert hasattr(result, 'image_count')
        assert hasattr(result, 'assets_dir')
