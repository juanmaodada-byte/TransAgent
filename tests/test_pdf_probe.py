"""D6 PDF probe command and gate tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from docx import Document

from backend.pipeline import pdf_probe
from backend.pipeline.pdf_probe import ConversionResult, PdfInspection, convert_pdf_to_docx, validate_docx_package
from scripts import d6_pdf_probe


def _minimal_docx(path: Path) -> None:
    doc = Document()
    doc.add_paragraph("hello")
    doc.save(path)


def test_worker_uses_fixed_parameter_array(monkeypatch, tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    output = tmp_path / "out.docx"
    captured = {}

    def fake_run(cmd, text, capture_output, timeout, check):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        _minimal_docx(output)
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_run)
    result = convert_pdf_to_docx(pdf, output, timeout=9)
    assert captured["cmd"] == [
        str(pdf_probe.PDF_RUNTIME),
        str(pdf_probe.PDF_TO_DOCX_WORKER),
        "--input",
        str(pdf),
        "--output",
        str(output),
    ]
    assert captured["timeout"] == 9
    assert result.engine == "pdf2docx 0.5.13"


def test_worker_never_uses_shell_true():
    source = Path("backend/pipeline/pdf_probe.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell = True" not in source


def test_conversion_timeout_is_identified(monkeypatch, tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.7")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_CONVERSION_ERROR: pdf2docx conversion timed out"):
        convert_pdf_to_docx(pdf, tmp_path / "out.docx", timeout=1)


def test_nonzero_exit_is_identified(monkeypatch, tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.7")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 7, "", "boom")

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_CONVERSION_ERROR: boom"):
        convert_pdf_to_docx(pdf, tmp_path / "out.docx")


def test_missing_empty_and_non_docx_outputs_are_rejected(monkeypatch, tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    output = tmp_path / "out.docx"

    def fake_missing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_missing)
    with pytest.raises(ValueError, match="DOCX output missing"):
        convert_pdf_to_docx(pdf, output)

    def fake_empty(cmd, **kwargs):
        output.write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_empty)
    with pytest.raises(ValueError, match="DOCX output is empty"):
        convert_pdf_to_docx(pdf, output)

    def fake_not_docx(cmd, **kwargs):
        output.write_bytes(b"not zip")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pdf_probe.subprocess, "run", fake_not_docx)
    with pytest.raises(ValueError, match="not a ZIP"):
        convert_pdf_to_docx(pdf, output)


def test_conversion_refuses_to_overwrite_input_pdf(tmp_path):
    pdf = tmp_path / "same.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    with pytest.raises(ValueError, match="overwrite input PDF"):
        convert_pdf_to_docx(pdf, pdf)


def test_probe_status_values_are_limited():
    assert d6_pdf_probe.ALLOWED_STATUS == {"GO", "NO-GO", "BLOCKED_BY_ENVIRONMENT"}


def test_environment_and_product_blockers_are_separate(monkeypatch):
    monkeypatch.setattr(d6_pdf_probe, "ensure_pdf_runtime", lambda: (_ for _ in ()).throw(ValueError("env bad")))
    status, data = d6_pdf_probe.run_probe()
    assert status == "BLOCKED_BY_ENVIRONMENT"
    assert data["environment_blockers"]
    assert not data["product_blockers"]


def test_structure_diff_causes_no_go(monkeypatch, tmp_path):
    fixture_dir = tmp_path / "pdf"
    fixture_dir.mkdir()
    for name in ["plain", "mixed", "columns", "scan", "hybrid", "corrupt", "disguised", "encrypted", "empty"]:
        (fixture_dir / f"d6_{name}.pdf").write_bytes(b"%PDF-1.7")

    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(d6_pdf_probe, "SOFFICE", str(tool))
    monkeypatch.setattr(d6_pdf_probe, "PDFINFO", str(tool))
    monkeypatch.setattr(d6_pdf_probe, "PDFTOPPM", str(tool))
    monkeypatch.setattr(d6_pdf_probe, "DEFAULT_BUNDLED_TIKAL", tool)
    monkeypatch.setattr(d6_pdf_probe, "ensure_pdf_runtime", lambda: {"python": "3.13", "executable": "py", "packages": {"pdf2docx": "0.5.13", "PyMuPDF": "1.28.2"}})
    monkeypatch.setattr(d6_pdf_probe, "write_runtime_manifest", lambda path, runtime: None)
    monkeypatch.setattr(d6_pdf_probe, "ensure_pdf_fixtures", lambda: fixture_dir)
    monkeypatch.setattr(d6_pdf_probe, "fixture_paths", lambda pdf_dir: {
        "plain": fixture_dir / "d6_plain.pdf",
        "mixed": fixture_dir / "d6_mixed.pdf",
        "columns": fixture_dir / "d6_columns.pdf",
        "scan": fixture_dir / "d6_scan.pdf",
        "hybrid": fixture_dir / "d6_hybrid.pdf",
        "corrupt": fixture_dir / "d6_corrupt.pdf",
        "disguised": fixture_dir / "d6_disguised.pdf",
        "encrypted": fixture_dir / "d6_encrypted.pdf",
        "empty": fixture_dir / "d6_empty.pdf",
    })

    def fake_inspect(path):
        name = path.name
        if "scan" in name or "empty" in name:
            return PdfInspection(True, False, 1, [0], [], [1], 0, "scan", "DOCUMENT_OCR_UNSUPPORTED")
        if "corrupt" in name or "disguised" in name:
            return PdfInspection(False, False, error_code="DOCUMENT_INTEGRITY_ERROR")
        if "encrypted" in name:
            return PdfInspection(True, True, 1, [], [], [], 0, "", "DOCUMENT_INTEGRITY_ERROR")
        if "hybrid" in name:
            return PdfInspection(True, False, 2, [10, 0], [1], [2], 10, "mixed", "", ["mixed warning"])
        return PdfInspection(True, False, 1, [10], [1], [], 10, "text", "")

    monkeypatch.setattr(d6_pdf_probe, "inspect_pdf", fake_inspect)
    monkeypatch.setattr(d6_pdf_probe, "require_convertible_pdf", fake_inspect)

    def fake_convert(pdf, out):
        _minimal_docx(out)
        return ConversionResult("pdf2docx 0.5.13", str(pdf), str(out), 0.1, False, [])

    monkeypatch.setattr(d6_pdf_probe, "convert_pdf_to_docx", fake_convert)
    monkeypatch.setattr(d6_pdf_probe, "validate_docx_package", lambda path: {"xml_file_count": 1})
    monkeypatch.setattr(d6_pdf_probe, "snapshot_docx_structure", lambda path: {"image_count": 0, "table_count": 0})
    monkeypatch.setattr(d6_pdf_probe, "render_pdf_to_pngs", lambda *args: {"pdf_pages": 1})
    monkeypatch.setattr(d6_pdf_probe, "render_docx_to_pdf_and_pngs", lambda *args: {"pdf_pages": 1})
    monkeypatch.setattr(d6_pdf_probe, "fallback_text_docx", lambda pdf, out: fake_convert(pdf, out))
    monkeypatch.setattr(d6_pdf_probe, "okapi_roundtrip", lambda *args: {"blocks_count": 1, "structure_diffs": {"image_count": {"before": 1, "after": 0}}})
    status, data = d6_pdf_probe.run_probe()
    assert status == "NO-GO"
    assert any("structure snapshot changed" in item for item in data["product_blockers"])


def test_scan_does_not_enter_pdf2docx(monkeypatch):
    called = []
    monkeypatch.setattr(d6_pdf_probe, "convert_pdf_to_docx", lambda *args, **kwargs: called.append(args))
    scan = PdfInspection(True, False, 1, [0], [], [1], 0, "scan", "DOCUMENT_OCR_UNSUPPORTED")
    assert scan.error_code == "DOCUMENT_OCR_UNSUPPORTED"
    assert called == []


def test_tests_do_not_call_llm_or_network():
    forbidden = ["open" + "ai", "request" + "s.", "http" + "x."]
    for path in [Path("tests/test_pdf_inspection.py"), Path("tests/test_pdf_fallback_docx.py"), Path("tests/test_pdf_probe.py")]:
        text = path.read_text(encoding="utf-8").lower()
        assert all(token not in text for token in forbidden)
