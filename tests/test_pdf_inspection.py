"""D6 PDF text-layer inspection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pipeline.pdf_probe import MIXED_TEXT_WARNING, ensure_pdf_fixtures, inspect_pdf, require_convertible_pdf


@pytest.fixture(scope="module")
def pdf_fixtures() -> Path:
    return ensure_pdf_fixtures()


def test_plain_text_pdf_is_text(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_plain_text.pdf")
    assert result.openable
    assert not result.encrypted
    assert result.classification == "text"
    assert result.page_count >= 2
    assert result.text_pages == [1, 2]
    assert result.total_text_chars > 100


def test_scanned_pdf_is_rejected_as_ocr_unsupported(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_scanned_image_only.pdf")
    assert result.classification == "scan"
    assert result.error_code == "DOCUMENT_OCR_UNSUPPORTED"
    assert result.no_text_pages == [1]
    with pytest.raises(ValueError, match="DOCUMENT_OCR_UNSUPPORTED"):
        require_convertible_pdf(pdf_fixtures / "d6_scanned_image_only.pdf")


def test_mixed_pdf_records_no_text_page_warning(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_mixed_text_and_scan.pdf")
    assert result.classification == "mixed"
    assert result.text_pages == [1]
    assert result.no_text_pages == [2]
    assert MIXED_TEXT_WARNING in result.warnings


def test_corrupt_pdf_is_rejected(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_corrupt_header.pdf")
    assert not result.openable
    assert result.error_code == "DOCUMENT_INTEGRITY_ERROR"


def test_disguised_non_pdf_is_rejected(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_disguised_text.pdf")
    assert not result.openable
    assert result.error_code == "DOCUMENT_INTEGRITY_ERROR"


def test_encrypted_pdf_is_rejected(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_encrypted.pdf")
    assert result.encrypted
    assert result.error_code == "DOCUMENT_INTEGRITY_ERROR"


def test_empty_page_does_not_count_as_text(pdf_fixtures):
    result = inspect_pdf(pdf_fixtures / "d6_empty_page.pdf")
    assert result.classification == "scan"
    assert result.error_code == "DOCUMENT_OCR_UNSUPPORTED"
    assert result.total_text_chars == 0
