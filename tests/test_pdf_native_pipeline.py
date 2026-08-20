"""PDF integration through production native document interfaces."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile

import pytest
from docx import Document

from transagent.interface import DocumentBlock, FormatType
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline import pdf_normalizer
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from backend.pipeline.pdf_probe import FALLBACK_WARNING, ensure_pdf_fixtures


def _translated(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    return [DocumentBlock(block_id=block.block_id, block_type=block.block_type, text=f"PDF译:{block.text}") for block in blocks]


def _write_docx(path: Path, text: str = "PDF source text") -> None:
    doc = Document()
    doc.add_paragraph(text)
    doc.save(path)


def _normalized(path: Path, fallback: bool = False) -> pdf_normalizer.NormalizedPdfDocx:
    _write_docx(path)
    return pdf_normalizer.NormalizedPdfDocx(
        path=path,
        engine="pymupdf text -> python-docx" if fallback else "pdf2docx 0.5.13",
        fallback_used=fallback,
        page_count=1,
        text_pages=[1],
        no_text_pages=[],
        total_text_chars=20,
        warnings=[pdf_normalizer.PDF_APPROXIMATE_WARNING] + ([FALLBACK_WARNING] if fallback else []),
        runtime_version="3.13.14",
        runtime={"python": "3.13.14", "pymupdf": "1.28.2", "pdf2docx": "0.5.13"},
        source_sha256="source-sha",
        normalized_docx_sha256="docx-sha",
    )


def test_pdf_route_calls_pdf_normalizer_once(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    work = tmp_path / "work"
    work.mkdir()
    calls = []

    monkeypatch.setattr(native, "detect_format", lambda path: SimpleNamespace(format_type=FormatType.PDF.value))
    monkeypatch.setattr(native, "_create_work_dir", lambda session_id: work)
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot_docx_structure", lambda path: {"table_count": 0})
    monkeypatch.setattr(native, "_validate_xliff_file", lambda path: None)
    monkeypatch.setattr(native, "_blocks_from_xliff", lambda path: [DocumentBlock(block_id="u1", text="A", block_type="text")])

    def fake_normalize(src, work_dir):
        calls.append((src, work_dir))
        return _normalized(work / "source-pdf-normalized.docx")

    def fake_run(cmd, cwd, timeout, error_code):
        Path(cmd[cmd.index("-od") + 1], f"{Path(cmd[-1]).name}.xlf").write_text("<x/>", encoding="utf-8")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "normalize_pdf_to_docx", fake_normalize)
    monkeypatch.setattr(native, "_run_tikal", fake_run)
    result = native.extract_document(str(source), session_id="safe")
    assert len(calls) == 1
    assert result.fidelity_level == "approximate"
    assert result.document_manifest.source_format == "pdf"
    assert result.document_manifest.normalized_from_doc is False
    assert result.document_manifest.conversion_metadata["engine"] == "pdf2docx 0.5.13"
    assert result.document_manifest.conversion_metadata["source_page_count"] == 1
    assert "PDF source text" not in repr(result.document_manifest.conversion_metadata)


def test_docx_and_doc_routes_do_not_call_pdf_normalizer(monkeypatch, tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")
    monkeypatch.setattr(native, "detect_format", lambda path: SimpleNamespace(format_type=FormatType.DOCX.value))
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot_docx_structure", lambda path: {})
    monkeypatch.setattr(native, "_validate_xliff_file", lambda path: None)
    monkeypatch.setattr(native, "_blocks_from_xliff", lambda path: [DocumentBlock(block_id="u1", text="A", block_type="text")])
    monkeypatch.setattr(native, "normalize_pdf_to_docx", lambda *args: pytest.fail("DOCX route called PDF normalizer"))

    def fake_run(cmd, cwd, timeout, error_code):
        Path(cmd[cmd.index("-od") + 1], f"{Path(cmd[-1]).name}.xlf").write_text("<x/>", encoding="utf-8")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    assert native.extract_document(str(source), session_id="safe").fidelity_level == "native"


def test_pdf_merge_requires_trusted_manifest(tmp_path):
    xliff = tmp_path / "source.docx.xlf"
    xliff.write_text("""<?xml version="1.0"?><xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2"><file><body><trans-unit id="u1"><source>A</source></trans-unit></body></file></xliff>""", encoding="utf-8")
    docx = tmp_path / "source.docx"
    _write_docx(docx)
    document = native.PreprocessResult(
        blocks=[DocumentBlock(block_id="u1", text="A", block_type="text")],
        normalized_docx_path=str(docx),
        xliff_path=str(xliff),
        fidelity_level="approximate",
        source_lang="en",
        target_lang="zh-CN",
        work_dir=str(tmp_path),
        original_structure_snapshot=snapshot_docx_structure(str(docx)),
        okapi_filter_config_id=native.CONFIG_ID,
        okapi_filter_config_sha256=native._sha256_file(native.CONFIG_SOURCE),
    )
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native.merge_translations(document, _translated(document.blocks))


@pytest.mark.integration
def test_real_pdf_extract_merge_docx_loop(tmp_path):
    pdf_dir = ensure_pdf_fixtures()
    doc = native.extract_document(str(pdf_dir / "d6_plain_text.pdf"), session_id="pdfplain")
    assert doc.fidelity_level == "approximate"
    assert doc.blocks
    assert len({block.block_id for block in doc.blocks}) == len(doc.blocks)
    output = Path(native.merge_translations(doc, _translated(doc.blocks), session_id="pdfplain"))
    assert output.exists()
    assert output.suffix == ".docx"
    assert output.resolve() != Path(doc.source_document_path).resolve()
    assert output.resolve() != Path(doc.normalized_docx_path).resolve()
    assert snapshot_docx_structure(str(output)) == doc.original_structure_snapshot
    with zipfile.ZipFile(output) as zf:
        text = "".join(ET.fromstring(zf.read("word/document.xml")).itertext())
        assert "PDF译:" in text
        assert "\ufffd" not in text
        assert all(b"[[TA_" not in zf.read(name) for name in zf.namelist() if name.endswith(".xml") or name.endswith(".rels"))


@pytest.mark.integration
def test_real_pdf_forced_fallback_extract_merge_docx_loop(monkeypatch):
    pdf_dir = ensure_pdf_fixtures()
    monkeypatch.setattr(
        pdf_normalizer,
        "_convert_with_pdf2docx",
        lambda *args: (_ for _ in ()).throw(pdf_normalizer.pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF to DOCX conversion failed")),
    )
    monkeypatch.setattr(
        native,
        "normalize_pdf_to_docx",
        pdf_normalizer.normalize_pdf_to_docx,
    )
    doc = native.extract_document(str(pdf_dir / "d6_plain_text.pdf"), session_id="pdffallback")
    assert doc.document_manifest.conversion_metadata["fallback_used"] is True
    assert FALLBACK_WARNING in doc.conversion_warnings
    output = Path(native.merge_translations(doc, _translated(doc.blocks), session_id="pdffallback"))
    assert output.exists()
    assert snapshot_docx_structure(str(output)) == doc.original_structure_snapshot


@pytest.mark.integration
def test_scan_pdf_is_rejected_by_production_interface():
    pdf_dir = ensure_pdf_fixtures()
    with pytest.raises(ValueError, match="DOCUMENT_OCR_UNSUPPORTED"):
        native.extract_document(str(pdf_dir / "d6_scanned_image_only.pdf"), session_id="pdfscan")
