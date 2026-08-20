from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from transagent.backend.pipeline import document_quality as quality
from transagent.backend.pipeline.document_quality import check_document_runtime_health


def test_document_runtime_health_contract():
    health = check_document_runtime_health()
    for key in [
        "java_17",
        "tikal_1_48_0",
        "okapi_config",
        "libreoffice",
        "pdf_runtime",
        "pymupdf_1_28_2",
        "pdf2docx_0_5_13",
        "python_docx",
        "pdfinfo",
        "pdftoppm",
        "pdftotext_or_pypdf",
        "cjk_font",
    ]:
        assert key in health
    assert health["cjk_font"]["status"] in {
        "preferred CJK font available",
        "fallback font available",
        "no suitable CJK font",
    }


def _write_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_health_check_uses_same_pdf_runtime_resolver(monkeypatch, tmp_path):
    runtime = _write_executable(tmp_path / "python")
    monkeypatch.setenv("PDF_RUNTIME_PYTHON", str(runtime))
    monkeypatch.setattr(
        quality.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="1.28.2\n", stderr=""),
    )
    assert quality._pdf_runtime_health()["ok"] is True
    assert quality._pdf_package_health("PyMuPDF", "1.28.2")["ok"] is True


def test_health_check_invalid_configured_pdf_runtime_is_false(monkeypatch, tmp_path):
    monkeypatch.setenv("PDF_RUNTIME_PYTHON", str(tmp_path / "missing-python"))
    health = check_document_runtime_health()
    assert health["pdf_runtime"]["ok"] is False
    assert health["pymupdf_1_28_2"]["ok"] is False
    assert health["ok"] is False


@pytest.mark.parametrize(
    ("env_name", "command_name"),
    [
        ("PDFINFO_PATH", "pdfinfo"),
        ("PDFTOPPM_PATH", "pdftoppm"),
        ("PDFTOTEXT_PATH", "pdftotext"),
    ],
)
def test_configured_poppler_runtime_invalid_fails_closed(monkeypatch, tmp_path, env_name, command_name):
    monkeypatch.setenv(env_name, str(tmp_path / "missing-tool"))
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        quality.resolve_poppler_tool(env_name, command_name)
    assert quality._poppler_health(env_name, command_name)["ok"] is False
