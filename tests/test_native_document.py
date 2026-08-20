"""D3 native DOCX pipeline tests."""

from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

import pytest

from transagent.interface import DocumentArtifactManifest, DocumentBlock, FormatType, PreprocessResult
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline.doc_normalizer import resolve_libreoffice
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline.xliff_codec import XLIFF_NS, qname


def write_xliff(path: Path, units: str) -> Path:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="{XLIFF_NS}" version="1.2">
  <file original="word/document.xml" source-language="en" target-language="zh-CN">
    <body>{units}</body>
  </file>
</xliff>
""",
        encoding="utf-8",
    )
    return path


def native_result(tmp_path, xliff: Path, blocks: list[DocumentBlock] | None = None) -> PreprocessResult:
    docx = tmp_path / "source.docx"
    docx.write_bytes(b"placeholder")
    return PreprocessResult(
        blocks=blocks or native._blocks_from_xliff(xliff),
        source_document_path=str(docx),
        normalized_docx_path=str(docx),
        xliff_path=str(xliff),
        fidelity_level="native",
        source_lang="en",
        target_lang="zh-CN",
        work_dir=str(tmp_path),
        original_structure_snapshot={},
        okapi_filter_config_id=native.CONFIG_ID,
        okapi_filter_config_sha256=native._sha256_file(native.CONFIG_SOURCE),
        schema_version="2.1",
    )


def translated(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    return [
        DocumentBlock(block_id=b.block_id, block_type=b.block_type, text=f"译:{b.text}")
        for b in blocks
    ]


def _skip_if_cjk_rendering_environment_error(exc: ValueError) -> None:
    if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE: CJK font rendering runtime is unavailable"):
        pytest.skip("CJK font rendering runtime unavailable")
    raise exc


def test_blocks_from_xliff_return_stable_unique_ids(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source></trans-unit><trans-unit id="u2"><source>B</source></trans-unit>',
    )
    blocks = native._blocks_from_xliff(xliff)
    assert [b.block_id for b in blocks] == ["u1", "u2"]
    assert len({b.block_id for b in blocks}) == 2
    assert [b.text for b in blocks] == ["A", "B"]


def test_duplicate_xliff_id_is_rejected(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source></trans-unit><trans-unit id="u1"><source>B</source></trans-unit>',
    )
    with pytest.raises(ValueError, match="DOCUMENT_EXTRACTION_ERROR"):
        native._blocks_from_xliff(xliff)


def test_translated_blocks_allow_reordered_fill(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source></trans-unit><trans-unit id="u2"><source>B</source></trans-unit>',
    )
    doc = native_result(tmp_path, xliff)
    by_id = native._validate_translated_blocks(list(reversed(translated(doc.blocks))), {b.block_id: b for b in doc.blocks})
    assert list(by_id) == ["u2", "u1"]


def test_missing_duplicate_and_unknown_ids_are_rejected(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source></trans-unit><trans-unit id="u2"><source>B</source></trans-unit>',
    )
    doc = native_result(tmp_path, xliff)
    source_by_id = {b.block_id: b for b in doc.blocks}
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native._validate_translated_blocks(translated(doc.blocks[:1]), source_by_id)
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native._validate_translated_blocks([translated(doc.blocks)[0], translated(doc.blocks)[0]], source_by_id)
    extra = translated(doc.blocks)
    extra[0].block_id = "unknown"
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native._validate_translated_blocks(extra, source_by_id)


def test_write_targets_restores_inline_xml_and_exact_target_count(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A <g id="1">bold</g> tail</source></trans-unit>',
    )
    doc = native_result(tmp_path, xliff)
    block = doc.blocks[0]
    target_text = block.text.replace("A ", "甲 ")
    native._write_targets(xliff, {block.block_id: block}, {block.block_id: DocumentBlock(block_id=block.block_id, text=target_text)}, "zh-CN")
    root = ET.parse(xliff).getroot()
    target = root.find(f".//{qname(XLIFF_NS, 'target')}")
    assert target is not None
    assert len(root.findall(f".//{qname(XLIFF_NS, 'target')}")) == 1
    assert "".join(target.itertext()) == "甲 bold tail"


def test_verify_written_targets_accepts_pure_chinese_inside_inline_format(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>Hello <g id="1">bold</g> tail</source></trans-unit>',
    )
    doc = native_result(tmp_path, xliff)
    block = doc.blocks[0]
    target_text = "你好 [[TA_G1_START]]加粗[[TA_G1_END]] 结尾"
    translated_block = DocumentBlock(block_id=block.block_id, text=target_text, metadata={})
    native._write_targets(xliff, {block.block_id: block}, {block.block_id: translated_block}, "zh-CN")
    native._verify_written_targets(xliff, {block.block_id: block}, {block.block_id: translated_block})
    target = ET.parse(xliff).getroot().find(f".//{qname(XLIFF_NS, 'target')}")
    assert target is not None
    assert "".join(target.itertext()) == "你好 加粗 结尾"
    assert "Hello" not in "".join(target.itertext())
    assert "bold" not in "".join(target.itertext())


def test_verify_written_targets_rejects_target_text_drift(tmp_path):
    xliff = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>Hello <g id="1">bold</g></source></trans-unit>',
    )
    doc = native_result(tmp_path, xliff)
    block = doc.blocks[0]
    target_text = "你好 [[TA_G1_START]]加粗[[TA_G1_END]]"
    translated_block = DocumentBlock(block_id=block.block_id, text=target_text, metadata={})
    native._write_targets(xliff, {block.block_id: block}, {block.block_id: translated_block}, "zh-CN")
    root = ET.parse(xliff).getroot()
    target = root.find(f".//{qname(XLIFF_NS, 'target')}")
    assert target is not None
    target.text = "你好 English leak "
    ET.ElementTree(root).write(xliff, encoding="utf-8", xml_declaration=True)
    with pytest.raises(ValueError, match="DOCUMENT_PLACEHOLDER_ERROR"):
        native._verify_written_targets(xliff, {block.block_id: block}, {block.block_id: translated_block})


def test_session_id_path_traversal_is_rejected():
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native._validate_session_id("../escape")


def test_merge_approximate_passes_readability_baseline_png_dir(monkeypatch, tmp_path):
    # Regression: the image-visibility gate needs the normalized pdf-readability renders
    # as the baseline; merge_translations must forward that directory for approximate,
    # but only when Layer 1 (normalization image visibility) already passed.
    xliff = write_xliff(tmp_path / "source.docx.xlf", '<trans-unit id="u1"><source>A</source></trans-unit>')
    doc = native_result(tmp_path, xliff)
    doc.fidelity_level = "approximate"
    doc.document_manifest = DocumentArtifactManifest(
        fidelity_level="approximate",
        source_format=FormatType.PDF.value,
        normalized_docx_path=doc.normalized_docx_path,
        normalized_docx_sha256="a" * 64,
        conversion_metadata={
            "engine": "test",
            "readability": {
                "image_visibility": {
                    "image_visibility_checked": True,
                    "meaningful_image_count": 1,
                    "matched_visible_image_count": 1,
                    "minimum_visible_area_ratio": 1.0,
                    "invisible_image_count": 0,
                }
            },
        },
    )
    readability_dir = tmp_path / "pdf-readability"
    readability_dir.mkdir()
    (readability_dir / "render_page-1.png").write_bytes(b"png")
    (readability_dir / "render.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
    source_docx = Path(doc.normalized_docx_path)

    captured = {}
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "apply_cjk_fonts", lambda path, **kwargs: {"applied": False, "reason": "test"})

    def fake_run(cmd, cwd, timeout, error_code):
        output = Path(cmd[cmd.index("-od") + 1]) / source_docx.name
        output.write_bytes(b"merged")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)

    def fake_validate(path, snapshot, work_dir, fidelity, baseline_png_dir=None):
        captured["fidelity"] = fidelity
        captured["baseline_png_dir"] = baseline_png_dir
        return type(
            "Quality",
            (),
            {"to_public_dict": lambda self: {"page_count": 1, "blank_pages": [], "warnings": []}},
        )()

    monkeypatch.setattr(native, "validate_delivery_docx", fake_validate)
    output = native.merge_translations(doc, [DocumentBlock(block_id="u1", block_type="text", text="译:A")])
    assert Path(output).exists()
    assert captured["fidelity"] == "approximate"
    assert captured["baseline_png_dir"] == readability_dir
    assert not (source_docx.resolve() == Path(output).resolve())


def test_merge_approximate_without_readability_dir_passes_none(monkeypatch, tmp_path):
    # No pdf-readability renders available -> baseline stays None and the gate is skipped.
    xliff = write_xliff(tmp_path / "source.docx.xlf", '<trans-unit id="u1"><source>A</source></trans-unit>')
    doc = native_result(tmp_path, xliff)
    doc.fidelity_level = "approximate"
    doc.document_manifest = DocumentArtifactManifest(
        fidelity_level="approximate",
        source_format=FormatType.PDF.value,
        normalized_docx_path=doc.normalized_docx_path,
        normalized_docx_sha256="b" * 64,
        conversion_metadata={"engine": "test"},
    )
    captured = {}
    source_docx = Path(doc.normalized_docx_path)
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "apply_cjk_fonts", lambda path, **kwargs: {"applied": False, "reason": "test"})

    def fake_run(cmd, cwd, timeout, error_code):
        output = Path(cmd[cmd.index("-od") + 1]) / source_docx.name
        output.write_bytes(b"merged")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    monkeypatch.setattr(
        native,
        "validate_delivery_docx",
        lambda path, snapshot, work_dir, fidelity, baseline_png_dir=None: captured.update(
            {"fidelity": fidelity, "baseline_png_dir": baseline_png_dir}
        ) or type(
            "Quality",
            (),
            {"to_public_dict": lambda self: {"page_count": 1, "blank_pages": [], "warnings": []}},
        )(),
    )
    output = native.merge_translations(doc, [DocumentBlock(block_id="u1", block_type="text", text="译:A")])
    assert Path(output).exists()
    assert captured["fidelity"] == "approximate"
    assert captured["baseline_png_dir"] is None


def test_merge_approximate_layer1_failed_keeps_baseline_none(monkeypatch, tmp_path):
    # Layer 1 rejected the normalization render (a figure was lost), so the
    # pdf-readability render must NOT be used as the Layer 2 baseline.
    xliff = write_xliff(tmp_path / "source.docx.xlf", '<trans-unit id="u1"><source>A</source></trans-unit>')
    doc = native_result(tmp_path, xliff)
    doc.fidelity_level = "approximate"
    doc.document_manifest = DocumentArtifactManifest(
        fidelity_level="approximate",
        source_format=FormatType.PDF.value,
        normalized_docx_path=doc.normalized_docx_path,
        normalized_docx_sha256="c" * 64,
        conversion_metadata={
            "engine": "test",
            "readability": {
                "image_visibility": {
                    "image_visibility_checked": True,
                    "meaningful_image_count": 1,
                    "matched_visible_image_count": 0,
                    "minimum_visible_area_ratio": 0.0,
                    "invisible_image_count": 1,
                }
            },
        },
    )
    readability_dir = tmp_path / "pdf-readability"
    readability_dir.mkdir()
    (readability_dir / "render_page-1.png").write_bytes(b"png")
    (readability_dir / "render.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
    captured = {}
    source_docx = Path(doc.normalized_docx_path)
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "apply_cjk_fonts", lambda path, **kwargs: {"applied": False, "reason": "test"})

    def fake_run(cmd, cwd, timeout, error_code):
        output = Path(cmd[cmd.index("-od") + 1]) / source_docx.name
        output.write_bytes(b"merged")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    monkeypatch.setattr(
        native,
        "validate_delivery_docx",
        lambda path, snapshot, work_dir, fidelity, baseline_png_dir=None: captured.update(
            {"fidelity": fidelity, "baseline_png_dir": baseline_png_dir}
        ) or type(
            "Quality",
            (),
            {"to_public_dict": lambda self: {"page_count": 1, "blank_pages": [], "warnings": []}},
        )(),
    )
    output = native.merge_translations(doc, [DocumentBlock(block_id="u1", block_type="text", text="译:A")])
    assert Path(output).exists()
    assert captured["fidelity"] == "approximate"
    assert captured["baseline_png_dir"] is None


def test_extract_document_uses_fixed_tikal_args_and_no_shell(monkeypatch, tmp_path):
    captured = {}
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")

    monkeypatch.setattr(native, "detect_format", lambda path: type("Fmt", (), {"format_type": FormatType.DOCX.value})())
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot_docx_structure", lambda path: {"table_count": 0})
    monkeypatch.setattr(native, "_validate_xliff_file", lambda path: None)
    monkeypatch.setattr(native, "_blocks_from_xliff", lambda path: [DocumentBlock(block_id="u1", text="A", block_type="text")])

    def fake_run(cmd, cwd, timeout, error_code):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["error_code"] = error_code
        Path(cmd[cmd.index("-od") + 1], f"{Path(cmd[-1]).name}.xlf").write_text("<x/>", encoding="utf-8")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    result = native.extract_document(str(source), session_id="safe")
    assert captured["cmd"][:9] == [
        str(native.TIKAL),
        "-x",
        "-fc",
        native.CONFIG_ID,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-od",
    ]
    assert captured["cmd"][-1] == result.normalized_docx_path
    assert captured["timeout"] == native.EXTRACT_TIMEOUT_SECONDS


def test_merge_does_not_overwrite_source_and_uses_fixed_args(monkeypatch, tmp_path):
    xliff = write_xliff(tmp_path / "source.docx.xlf", '<trans-unit id="u1"><source>A</source></trans-unit>')
    doc = native_result(tmp_path, xliff)
    doc.document_manifest = DocumentArtifactManifest(fidelity_level="native")
    source_docx = Path(doc.normalized_docx_path)

    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "apply_cjk_fonts", lambda path, **kwargs: {"applied": False, "reason": "test"})
    monkeypatch.setattr(
        native,
        "validate_delivery_docx",
        lambda path, snapshot, work_dir, fidelity, baseline_png_dir=None: type(
            "Quality",
            (),
            {"to_public_dict": lambda self: {"page_count": 2, "blank_pages": [2], "warnings": ["blank page warning"]}},
        )(),
    )

    captured = {}

    def fake_run(cmd, cwd, timeout, error_code):
        captured["cmd"] = cmd
        output = Path(cmd[cmd.index("-od") + 1]) / source_docx.name
        output.write_bytes(b"docx")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    output = native.merge_translations(doc, translated(doc.blocks), session_id="safe")
    assert Path(output) != source_docx
    assert doc.document_manifest.delivery_quality["blank_pages"] == [2]
    assert "blank page warning" in doc.document_manifest.delivery_quality["warnings"]
    assert captured["cmd"][:9] == [
        str(native.TIKAL),
        "-m",
        "-fc",
        native.CONFIG_ID,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-sd",
    ]


def test_residual_placeholder_and_structure_diff_are_rejected(tmp_path, cloud_native_docx_path):
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("word/document.xml", "<w:document xmlns:w='w'>[[TA_X1]]</w:document>")
        zf.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(ValueError, match="DOCUMENT_PLACEHOLDER_ERROR"):
        native._validate_output_docx(bad, {})

    expected = snapshot_docx_structure(cloud_native_docx_path)
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        native._validate_output_docx(Path(cloud_native_docx_path), {**expected, "section_count": expected["section_count"] + 1})


@pytest.mark.integration
def test_d3_real_docx_extract_merge_render_loop(okapi_probe_docx_path, tmp_path):
    try:
        soffice = resolve_libreoffice().executable
    except ValueError:
        pytest.skip("LibreOffice runtime unavailable")
    if not native.TIKAL.exists():
        pytest.skip("Okapi/LibreOffice runtime unavailable")
    doc = native.extract_document(okapi_probe_docx_path, session_id="integration")
    translated_blocks = []
    for block in doc.blocks:
        new_text = "D3:" + block.text
        translated_blocks.append(DocumentBlock(block_id=block.block_id, block_type=block.block_type, text=new_text))
    try:
        output = Path(native.merge_translations(doc, translated_blocks, session_id="integration"))
    except ValueError as exc:
        _skip_if_cjk_rendering_environment_error(exc)
    assert output.exists() and output.stat().st_size > 0
    assert snapshot_docx_structure(str(output)) == doc.original_structure_snapshot

    with zipfile.ZipFile(output) as zf:
        texts = {name: ET.fromstring(zf.read(name)) for name in zf.namelist() if name.endswith(".xml")}
    document_text = "".join(texts["word/document.xml"].itertext())
    header_text = "\n".join("".join(node.itertext()) for name, node in texts.items() if name.startswith("word/header"))
    footer_text = "\n".join("".join(node.itertext()) for name, node in texts.items() if name.startswith("word/footer"))
    assert "D3:" in document_text
    assert "D2_HEADER_MARK_6F8B" in header_text and "译:D2_HEADER_MARK_6F8B" not in header_text
    assert "D2_FOOTER_MARK_91C2" in footer_text and "译:D2_FOOTER_MARK_91C2" not in footer_text

    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    result = native.subprocess.run(
        [str(soffice), "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(output)],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0
    pdf = pdf_dir / f"{output.stem}.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0
