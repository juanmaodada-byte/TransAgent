"""Production PDF normalizer tests for D7."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

from transagent.interface import FormatType
from transagent.backend.pipeline import pdf_normalizer as norm
from backend.pipeline.pdf_probe import ensure_pdf_fixtures


def _pdf_fmt(size: int = 100, page_count: int = 1):
    return SimpleNamespace(format_type=FormatType.PDF.value, size_bytes=size, page_count=page_count)


def _write_docx(path: Path, text: str = "hello") -> None:
    doc = Document()
    doc.add_paragraph(text)
    doc.save(path)


def _inspection(**overrides) -> norm.PdfInspection:
    data = dict(
        openable=True,
        encrypted=False,
        page_count=1,
        page_text_char_counts=[10],
        text_pages=[1],
        no_text_pages=[],
        total_text_chars=10,
        classification="text",
        error_code="",
        warnings=[],
    )
    data.update(overrides)
    return norm.PdfInspection(**data)


def _runtime():
    return {
        "python": "3.13.14",
        "packages": {"PyMuPDF": "1.28.2", "pdf2docx": "0.5.13", "python-docx": "1.2.0"},
    }


def _readability(metadata=None, warnings=None):
    return SimpleNamespace(metadata=metadata or {"content_retention_ratio": 1.0}, warnings=warnings or [])


def _write_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _stub_readability(monkeypatch):
    monkeypatch.setattr(
        norm,
        "_audit_pdf_readability",
        lambda *args: _readability(),
    )


def test_text_pdf_uses_pdf2docx_route(tmp_path):
    pdf_dir = ensure_pdf_fixtures()
    result = norm.normalize_pdf_to_docx(pdf_dir / "d6_plain_text.pdf", tmp_path)
    assert result.path.exists()
    assert result.engine == "pdf2docx 0.5.13"
    assert result.fallback_used is False
    assert result.page_count >= 2
    assert norm.PDF_APPROXIMATE_WARNING in result.warnings
    assert result.source_sha256
    assert result.normalized_docx_sha256
    assert result.readability["pdf_layout_fix"]["anchor_count_after"] == 0


def test_mixed_pdf_keeps_warning_and_no_text_pages(tmp_path):
    pdf_dir = ensure_pdf_fixtures()
    result = norm.normalize_pdf_to_docx(pdf_dir / "d6_mixed_text_and_scan.pdf", tmp_path)
    assert norm.MIXED_TEXT_WARNING in result.warnings
    assert result.text_pages == [1]
    assert result.no_text_pages == [2]


@pytest.mark.parametrize(
    ("inspection", "match"),
    [
        (_inspection(text_pages=[], no_text_pages=[1], total_text_chars=0, classification="scan"), "DOCUMENT_OCR_UNSUPPORTED"),
        (_inspection(openable=False, error_code="DOCUMENT_INTEGRITY_ERROR"), "DOCUMENT_INTEGRITY_ERROR"),
        (_inspection(encrypted=True, error_code="DOCUMENT_INTEGRITY_ERROR"), "DOCUMENT_INTEGRITY_ERROR"),
        (_inspection(page_count=0), "DOCUMENT_INTEGRITY_ERROR"),
        (_inspection(text_pages=[], no_text_pages=[1], total_text_chars=0, classification="scan"), "DOCUMENT_OCR_UNSUPPORTED"),
    ],
)
def test_unconvertible_pdf_is_rejected_before_conversion(monkeypatch, tmp_path, inspection, match):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    called = False
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: inspection)

    def fake_convert(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(norm, "_convert_with_pdf2docx", fake_convert)
    with pytest.raises(ValueError, match=match):
        norm.normalize_pdf_to_docx(source, tmp_path)
    assert called is False


def test_disguised_pdf_is_rejected_by_detect_format(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(ValueError, match="DOCUMENT_FORMAT_MISMATCH|DOCUMENT_UNSUPPORTED_FORMAT|DOCUMENT_INTEGRITY_ERROR"):
        norm.normalize_pdf_to_docx(source, tmp_path)


def test_size_and_page_limits_are_rejected(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt(size=norm.MAX_INPUT_PDF_BYTES + 1))
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        norm.normalize_pdf_to_docx(source, tmp_path)

    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: _inspection(page_count=norm.MAX_PDF_PAGES + 1))
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        norm.normalize_pdf_to_docx(source, tmp_path)


def test_worker_commands_are_fixed_arrays_and_no_shell():
    pdf = Path("/tmp/source.pdf")
    out = Path("/tmp/out.docx")
    assert norm.build_convert_command(pdf, out) == [
        str(norm.resolve_pdf_runtime()),
        str(norm.PDF_TO_DOCX_WORKER),
        "--input",
        str(pdf),
        "--output",
        str(out),
    ]
    assert norm.build_fallback_command(pdf, out)[1] == str(norm.PDF_TEXT_DOCX_WORKER)
    source = Path("backend/pipeline/pdf_normalizer.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell = True" not in source


def test_configured_pdf_runtime_takes_priority(monkeypatch, tmp_path):
    configured = _write_executable(tmp_path / "python")
    monkeypatch.setenv("PDF_RUNTIME_PYTHON", str(configured))
    assert norm.resolve_pdf_runtime() == configured.absolute()
    assert norm.build_inspect_command(Path("/tmp/source.pdf"))[0] == str(configured.absolute())


def test_configured_pdf_runtime_invalid_fails_closed(monkeypatch, tmp_path):
    fallback = _write_executable(tmp_path / "fallback-python")
    monkeypatch.setattr(norm, "DEFAULT_PDF_RUNTIME", fallback)
    monkeypatch.setenv("PDF_RUNTIME_PYTHON", str(tmp_path / "missing-python"))
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        norm.resolve_pdf_runtime()


def test_default_project_pdf_runtime_fallback(monkeypatch, tmp_path):
    fallback = _write_executable(tmp_path / "project-python")
    monkeypatch.delenv("PDF_RUNTIME_PYTHON", raising=False)
    monkeypatch.setattr(norm, "DEFAULT_PDF_RUNTIME", fallback)
    assert norm.resolve_pdf_runtime() == fallback.absolute()


def test_missing_pdf_runtime_returns_stable_error(monkeypatch, tmp_path):
    monkeypatch.delenv("PDF_RUNTIME_PYTHON", raising=False)
    monkeypatch.setattr(norm, "DEFAULT_PDF_RUNTIME", tmp_path / "missing-python")
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        norm.resolve_pdf_runtime()


@pytest.mark.parametrize("failure_message", [
    "PDF to DOCX conversion timed out",
    "PDF to DOCX conversion failed",
    "converted DOCX is invalid",
])
def test_conversion_failures_trigger_fallback(monkeypatch, tmp_path, failure_message):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    output_seen = {}
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: _inspection())

    def fake_convert(src, out):
        raise norm.pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR" if "invalid" in failure_message else "DOCUMENT_CONVERSION_ERROR", failure_message)

    def fake_fallback(src, out):
        output_seen["path"] = out
        _write_docx(out, "fallback")

    monkeypatch.setattr(norm, "_convert_with_pdf2docx", fake_convert)
    monkeypatch.setattr(norm, "_fallback_text_docx", fake_fallback)
    result = norm.normalize_pdf_to_docx(source, tmp_path)
    assert result.fallback_used is True
    assert result.path == output_seen["path"]
    assert norm.FALLBACK_WARNING in result.warnings


def test_inspection_failure_does_not_trigger_fallback(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: (_ for _ in ()).throw(norm.pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF inspection failed")))
    monkeypatch.setattr(norm, "_fallback_text_docx", lambda *args: pytest.fail("inspection failure entered fallback"))
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        norm.normalize_pdf_to_docx(source, tmp_path)


def test_public_error_does_not_leak_worker_output(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    secret = "/secret/runtime/path leaked PDF TEXT"
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: _inspection())

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 9, secret, secret)

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_CONVERSION_ERROR") as err:
        norm.normalize_pdf_to_docx(source, tmp_path)
    assert secret not in str(err.value)
    assert "/secret/runtime/path" not in str(err.value)
    assert "PDF TEXT" not in str(err.value)


def test_conversion_does_not_overwrite_input_pdf(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF")
    monkeypatch.setattr(norm, "detect_format", lambda path: _pdf_fmt())
    monkeypatch.setattr(norm, "ensure_pdf_runtime", _runtime)
    monkeypatch.setattr(norm, "inspect_pdf", lambda path: _inspection())
    monkeypatch.setattr(norm, "_safe_output_name", lambda name: "source.pdf")
    with pytest.raises(ValueError, match="overwrite source PDF"):
        norm.normalize_pdf_to_docx(source, tmp_path)
