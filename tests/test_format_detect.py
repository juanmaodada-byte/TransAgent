"""
格式检测与输入测试
===================
D1 测试基线 — MD/TXT/DOCX 识别、未知格式拒绝、损坏文件处理。

不依赖 API key、网络或真实 LLM。
"""
import pytest
import os
import zipfile
from tests.conftest import FIXTURES_DIR
from transagent.interface import FormatType


EXCEPTION_DIR = os.path.join(FIXTURES_DIR, "format_exceptions")


# ══════════════════════════════════════════════════════════════════
# 格式检测 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestFormatDetection:
    def test_detect_md(self, kubernetes_md, tmp_path):
        """MD 文件被正确识别为 MARKDOWN"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.format_type == FormatType.MARKDOWN.value

    def test_detect_txt(self, tmp_path):
        """TXT 文件被正确识别"""
        f = tmp_path / "test.txt"
        f.write_text("hello world", encoding="utf-8")
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.format_type == FormatType.TEXT.value

    def test_detect_docx(self, cloud_native_docx_path):
        """DOCX 文件被正确识别"""
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(cloud_native_docx_path)
        assert result.format_type == FormatType.DOCX.value
        assert result.metadata["detector"] == "ooxml-wordprocessingml"

    def test_detect_pdf(self):
        """PDF 文件由 header + PyMuPDF 解析确认"""
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(os.path.join(EXCEPTION_DIR, "minimal.pdf"))
        assert result.format_type == FormatType.PDF.value
        assert result.page_count == 1

    def test_detect_real_ole_word_doc(self):
        """真实 CFB/OLE2 WordDocument stream 被识别为 DOC"""
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(os.path.join(EXCEPTION_DIR, "minimal_word.doc"))
        assert result.format_type == FormatType.DOC.value
        assert result.metadata["detector"] == "ole2-worddocument"
        assert "WordDocument" in result.metadata["ole_streams"]

    def test_detect_returns_format_result(self, kubernetes_md, tmp_path):
        """detect_format 返回 FormatResult，含 size_bytes"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from transagent.backend.pipeline.preprocess import detect_format
        result = detect_format(str(f))
        assert result.size_bytes > 0
        assert result.metadata is not None

    def test_detect_nonexistent_file(self):
        """不存在的文件应抛出 FileNotFoundError"""
        from transagent.backend.pipeline.preprocess import detect_format
        with pytest.raises(FileNotFoundError):
            detect_format("/nonexistent/path/file.txt")


# ══════════════════════════════════════════════════════════════════
# 格式检测 — 失败用例
# ══════════════════════════════════════════════════════════════════

class TestFormatDetectionFailures:
    def test_detect_unsupported_format(self, unsupported_ext_file):
        """未支持格式应明确拒绝，不能 fallback 到 TEXT"""
        from transagent.backend.pipeline.preprocess import detect_format
        with pytest.raises(ValueError, match="DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(unsupported_ext_file)

    def test_detect_doc_not_docx(self, dot_doc_file):
        """.doc 的 OLE2 头不足以确认 WordDocument 时被拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR|DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(dot_doc_file)

    def test_detect_mismatched_extension_rejected(self):
        """扩展名与真实格式冲突应稳定拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = os.path.join(EXCEPTION_DIR, "pdf_named_docx.docx")
        with pytest.raises(ValueError, match="DOCUMENT_FORMAT_MISMATCH"):
            detect_format(path)

    def test_corrupt_docx_rejected_by_detection(self, corrupt_docx_file):
        """损坏 DOCX 在格式检测阶段即被拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
            detect_format(corrupt_docx_file)

    def test_plain_zip_not_docx(self):
        """普通 ZIP 冒充 DOCX 时不应通过 WordprocessingML 检测"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = os.path.join(EXCEPTION_DIR, "plain_zip.docx")
        with pytest.raises(ValueError, match="DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(path)

    def test_fake_ole_not_doc(self):
        """OLE2 不会被一律误判为 DOC"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = os.path.join(EXCEPTION_DIR, "fake_ole.doc")
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR|DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(path)

    def test_non_word_ole_rejected(self):
        """真实非 Word OLE2 容器不被识别为 DOC"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = os.path.join(EXCEPTION_DIR, "workbook_named_doc.doc")
        with pytest.raises(ValueError, match="DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(path)

    def test_encrypted_ole_rejected(self):
        """明显加密 OLE2 容器被拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = os.path.join(EXCEPTION_DIR, "encrypted_ole.doc")
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
            detect_format(path)

    def test_ole_directory_sparse_indexes_preserved(self):
        """空 directory entry 不会压缩后续原始索引导致 WordDocument 丢失"""
        from transagent.backend.pipeline.preprocess import _list_ole_streams
        path = os.path.join(EXCEPTION_DIR, "minimal_word.doc")
        streams = _list_ole_streams(path)
        assert ["WordDocument"] in streams

    def test_docm_content_type_rejected(self, cloud_native_docx_path, tmp_path):
        """宏文档 content type 即使扩展名为 .docx 也拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = tmp_path / "macro.docx"
        _rewrite_docx_content_types(
            cloud_native_docx_path,
            path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            "application/vnd.ms-word.document.macroEnabled.main+xml",
        )
        with pytest.raises(ValueError, match="DOCUMENT_UNSUPPORTED_FORMAT"):
            detect_format(str(path))

    def test_encrypted_ooxml_rejected(self, tmp_path):
        """明显加密 OOXML 包被拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = tmp_path / "encrypted.docx"
        content_types = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.encryptedPackage"/>'
            '</Types>'
        )
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", content_types)
            zf.writestr("word/document.xml", "<w:document/>")
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
            detect_format(str(path))

    def test_docx_zip_path_traversal_rejected(self, cloud_native_docx_path, tmp_path):
        """DOCX ZIP 成员路径穿越被拒绝"""
        from transagent.backend.pipeline.preprocess import detect_format
        path = tmp_path / "traversal.docx"
        with zipfile.ZipFile(cloud_native_docx_path, "r") as src, zipfile.ZipFile(path, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("../evil.txt", "nope")
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
            detect_format(str(path))


def _rewrite_docx_content_types(src_path, dst_path, old, new):
    with zipfile.ZipFile(src_path, "r") as src, zipfile.ZipFile(dst_path, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "[Content_Types].xml":
                data = data.decode("utf-8").replace(old, new).encode("utf-8")
            dst.writestr(name, data)

    def test_corrupt_docx_fails_cleanly(self, corrupt_docx_file):
        """损坏 DOCX 应产生包含 DOCUMENT_INTEGRITY_ERROR 的明确错误"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
            convert_to_md(corrupt_docx_file, FormatType.DOCX.value)

    def test_illegal_encoding_fails_cleanly(self, illegal_txt_encoding):
        """非法编码文件应产生包含编码提示和 DOCUMENT_INTEGRITY_ERROR 的结构化错误"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        with pytest.raises(ValueError) as exc_info:
            convert_to_md(illegal_txt_encoding, FormatType.TEXT.value)
        msg = str(exc_info.value)
        has_encoding = any(w in msg.lower() for w in ["encoding", "decode", "utf", "charset", "编码"])
        has_error_code = "DOCUMENT_INTEGRITY_ERROR" in msg
        assert has_encoding and has_error_code, (
            f"Error should mention encoding AND contain DOCUMENT_INTEGRITY_ERROR. "
            f"Got: {msg[:200]}"
        )


# ══════════════════════════════════════════════════════════════════
# 输入转换 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestInputConversion:
    def test_convert_md_passthrough(self, kubernetes_md, tmp_path):
        """MD 输入直接通过，内容不变"""
        f = tmp_path / "test.md"
        f.write_text(kubernetes_md, encoding="utf-8")
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        result = convert_to_md(str(f), FormatType.MARKDOWN.value)
        assert result.md_text == kubernetes_md
        assert result.image_count == 0

    def test_convert_txt_passthrough(self, tmp_path):
        """TXT 输入直接通过"""
        f = tmp_path / "test.txt"
        f.write_text("plain text", encoding="utf-8")
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        result = convert_to_md(str(f), FormatType.TEXT.value)
        assert result.md_text == "plain text"

    def test_convert_docx_produces_text(self, cloud_native_docx_path):
        """DOCX 转换产生非空 MD 文本"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert len(result.md_text) > 0, "DOCX conversion produced empty text"
        assert result.image_count >= 1, "Expected at least 1 image from fixture"

    def test_convert_docx_has_assets_dir(self, cloud_native_docx_path):
        """DOCX 转换产生 assets_dir"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        assert result.assets_dir != "", "DOCX conversion should produce assets_dir"

    def test_convert_docx_returns_convert_result(self, cloud_native_docx_path):
        """convert_to_md 返回 ConvertResult"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType
        result = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)
        # ConvertResult 至少要有 md_text
        assert hasattr(result, 'md_text')
        assert hasattr(result, 'image_count')
        assert hasattr(result, 'assets_dir')
