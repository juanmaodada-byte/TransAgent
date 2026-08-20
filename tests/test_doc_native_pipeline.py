from pathlib import Path
from xml.etree import ElementTree as ET
import re
import zipfile

import pytest

from transagent.interface import DocumentBlock, FormatType
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline import doc_normalizer
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline.preprocess import detect_format


def _translated_blocks(blocks):
    return [
        DocumentBlock(block_id=block.block_id, block_type=block.block_type, text=f"DOC译:{block.text}")
        for block in blocks
    ]


def _skip_if_cjk_rendering_environment_error(exc: ValueError) -> None:
    if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE: CJK font rendering runtime is unavailable"):
        pytest.skip("CJK font rendering runtime unavailable")
    raise exc


def test_real_doc_fixture_is_verified_word_ole2(real_word_97_doc_path):
    result = detect_format(real_word_97_doc_path)
    assert result.format_type == FormatType.DOC.value
    assert result.metadata["detector"] == "ole2-worddocument"
    assert "WordDocument" in result.metadata["ole_streams"]


@pytest.mark.integration
def test_real_doc_normalize_extract_merge_render_loop(real_word_97_doc_path, tmp_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    soffice = doc_normalizer.resolve_libreoffice().executable

    doc = native.extract_document(real_word_97_doc_path, session_id="docnative")
    normalized = Path(doc.normalized_docx_path)
    assert doc.blocks
    assert len({block.block_id for block in doc.blocks}) == len(doc.blocks)
    assert doc.fidelity_level == "normalized"
    assert doc.source_document_path == real_word_97_doc_path
    assert normalized.exists() and normalized.stat().st_size > 0
    assert normalized.suffix.lower() == ".docx"
    assert doc.document_manifest.source_format == "doc"
    assert doc.document_manifest.normalized_from_doc is True
    assert doc.document_manifest.normalized_docx_sha256
    assert doc.document_manifest.libreoffice_executable_path
    assert doc.document_manifest.libreoffice_version
    assert doc.conversion_warnings

    before = snapshot_docx_structure(str(normalized))
    try:
        output = Path(native.merge_translations(doc, _translated_blocks(doc.blocks), session_id="docnative"))
    except ValueError as exc:
        _skip_if_cjk_rendering_environment_error(exc)
    after = snapshot_docx_structure(str(output))

    assert output.exists() and output.stat().st_size > 0
    assert output.suffix.lower() == ".docx"
    assert output.resolve() != Path(real_word_97_doc_path).resolve()
    assert output.resolve() != normalized.resolve()
    assert before["images"] == after["images"]
    assert before["table_count"] == after["table_count"]
    assert before["table_xml_counts"] == after["table_xml_counts"]
    assert before["section_count"] == after["section_count"]
    assert before["header_footer_relationships"] == after["header_footer_relationships"]

    with zipfile.ZipFile(output) as zf:
        xml_text = "\n".join(
            zf.read(name).decode("utf-8", errors="ignore")
            for name in zf.namelist()
            if name.endswith(".xml")
        )
        document_text = "".join(ET.fromstring(zf.read("word/document.xml")).itertext())
    assert "DOC译:" in document_text
    assert not re.search(r"\[\[TA_[A-Z0-9]+(?:_START|_END)?\]\]", xml_text)

    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    profile = tmp_path / "lo-render-profile"
    profile.mkdir()
    completed = native.subprocess.run(
        [
            str(soffice),
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_dir),
            str(output),
        ],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0
    pdf = pdf_dir / f"{output.stem}.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0


def test_docx_regression_stays_native_and_skips_normalizer(monkeypatch, okapi_probe_docx_path):
    called = False

    def fake_normalize(source, work_dir):
        nonlocal called
        called = True

    monkeypatch.setattr(native, "normalize_doc_to_docx", fake_normalize)
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    result = native.extract_document(okapi_probe_docx_path, session_id="docxregression")
    assert result.fidelity_level == "native"
    assert result.document_manifest.normalized_from_doc is False
    assert called is False
