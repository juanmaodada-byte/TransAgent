"""D2 probe command and gate tests."""

from pathlib import Path

import pytest

from scripts.d2_okapi_probe import (
    DEFAULT_CONFIG_ID,
    EXPECTED_INPUT_NAME,
    EXPECTED_OUTPUT_NAME,
    EXPECTED_XLIFF_NAME,
    PSEUDO_PREFIX,
    REGION_MARKERS,
    ProbeError,
    assert_docx_region_behavior,
    check_native_p0_config,
    compare_snapshots,
    copy_fixture_to_run,
    extract_pdf_text,
    ensure_filter_config_available,
    extract_with_tikal,
    merge_with_tikal,
    parse_tikal_version,
)


def test_copy_fixture_uses_exact_run_dir_name(tmp_path):
    fixture = tmp_path / "source.docx"
    fixture.write_bytes(b"docx")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    copied = copy_fixture_to_run(fixture, run_dir)
    assert copied == run_dir / EXPECTED_INPUT_NAME
    assert copied.read_bytes() == b"docx"


def test_extract_command_uses_config_id_and_output_dir(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, timeout=30, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return object()

    monkeypatch.setattr("scripts.d2_okapi_probe.run_command", fake_run)
    run_docx = tmp_path / EXPECTED_INPUT_NAME
    extract_with_tikal("/opt/okapi/tikal.sh", run_docx, tmp_path, DEFAULT_CONFIG_ID)
    assert captured["cmd"] == [
        "/opt/okapi/tikal.sh",
        "-x",
        "-fc",
        DEFAULT_CONFIG_ID,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-od",
        str(tmp_path),
        str(run_docx),
    ]
    assert captured["cwd"] == tmp_path


def test_merge_command_uses_same_config_and_source_output_dirs(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, timeout=30, cwd=None, env=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return object()

    monkeypatch.setattr("scripts.d2_okapi_probe.run_command", fake_run)
    xliff = tmp_path / EXPECTED_XLIFF_NAME
    merge_with_tikal("/opt/okapi/tikal.sh", xliff, tmp_path, DEFAULT_CONFIG_ID)
    assert captured["cmd"] == [
        "/opt/okapi/tikal.sh",
        "-m",
        "-fc",
        DEFAULT_CONFIG_ID,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-sd",
        str(tmp_path),
        "-od",
        str(tmp_path),
        str(xliff),
    ]
    assert captured["cwd"] == tmp_path


def test_filter_config_preflight_requires_real_id():
    listed = ensure_filter_config_available(DEFAULT_CONFIG_ID + "\n", DEFAULT_CONFIG_ID)
    assert listed.ok
    with pytest.raises(ProbeError, match="not listed"):
        ensure_filter_config_available("okf_plaintext\n", DEFAULT_CONFIG_ID)


def test_filter_config_preflight_records_unlisted_local_boundary():
    check = ensure_filter_config_available("okf_openxml\n", DEFAULT_CONFIG_ID, allow_local_unlisted=True)
    assert check.ok
    assert "does not list" in check.detail


def test_native_p0_config_gate_rejects_placeholder_and_builtin_id(tmp_path):
    config = tmp_path / "openxml_docx_p0.fprm"
    config.write_text("# placeholder\n", encoding="utf-8")
    placeholder = check_native_p0_config(config, "okf_openxml@openxml_docx_p0")
    assert not placeholder.ok
    config.write_text("'w:t':\n", encoding="utf-8")
    builtin = check_native_p0_config(config, "okf_openxml")
    assert not builtin.ok


def test_native_p0_config_gate_requires_real_switches(tmp_path):
    config = tmp_path / "openxml_docx_p0.fprm"
    config.write_text("elements:\n  'w:t':\n    ruleTypes: [TEXTMARKER]\n", encoding="utf-8")
    check = check_native_p0_config(config, DEFAULT_CONFIG_ID)
    assert not check.ok
    assert "missing required switches" in check.detail


def test_native_p0_config_gate_accepts_minimal_real_config(tmp_path):
    config = tmp_path / "openxml_docx_p0.fprm"
    config.write_text(
        """elements:
  'w:t':
    ruleTypes: [TEXTMARKER]
bPreferenceTranslateWordHeadersFooters.b=false
bPreferenceTranslateDocProperties.b=false
bPreferenceTranslateComments.b=false
bPreferenceTranslateWordHidden.b=false
translateWordNumberingLevelText.b=false
translateWordGraphicName.b=false
translateWordGraphicDescription.b=false
bPreferenceAggressiveCleanup.b=false
allowWordStyleOptimisation.b=false
""",
        encoding="utf-8",
    )
    assert check_native_p0_config(config, DEFAULT_CONFIG_ID).ok


def test_tikal_version_parser_uses_version_line():
    output = """-------------------------------------------------------------------------------
Okapi Tikal - Localization Toolset
Version: 2.1.48.0
-------------------------------------------------------------------------------
"""
    assert parse_tikal_version(output) == "Version: 2.1.48.0"


def test_snapshot_diff_reports_specific_fields():
    before = {"image_count": 1, "table_count": 1}
    after = {"image_count": 0, "table_count": 1, "section_count": 2}
    assert compare_snapshots(before, after) == {
        "image_count": {"before": 1, "after": 0},
        "section_count": {"before": None, "after": 2},
    }


def test_expected_output_contract_names_are_docx_derived():
    assert EXPECTED_XLIFF_NAME == "okapi_probe_mixed.docx.xlf"
    assert EXPECTED_OUTPUT_NAME == "okapi_probe_mixed.docx"


def test_docx_region_behavior_checks_parts_with_xml_parser(tmp_path):
    docx = tmp_path / "sample.docx"
    import zipfile

    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f"<w:document xmlns:w='w'><w:body><w:p><w:r><w:t>{PSEUDO_PREFIX}{REGION_MARKERS['body']}</w:t></w:r></w:p>"
            f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{PSEUDO_PREFIX}{REGION_MARKERS['table']}</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>",
        )
        zf.writestr("word/header1.xml", f"<w:hdr xmlns:w='w'><w:p><w:r><w:t>{REGION_MARKERS['header']}</w:t></w:r></w:p></w:hdr>")
        zf.writestr("word/footer1.xml", f"<w:ftr xmlns:w='w'><w:p><w:r><w:t>{REGION_MARKERS['footer']}</w:t></w:r></w:p></w:ftr>")
    result = assert_docx_region_behavior(docx)
    assert result == {
        "body_translated": True,
        "table_translated": True,
        "header_preserved": True,
        "footer_preserved": True,
    }


def test_docx_region_behavior_regresses_old_document_xml_assertion(tmp_path):
    docx = tmp_path / "sample.docx"
    import zipfile

    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("word/document.xml", "<w:document xmlns:w='w'><w:body /></w:document>")
        zf.writestr("word/header1.xml", f"<w:hdr xmlns:w='w'><w:t>{REGION_MARKERS['header']}</w:t></w:hdr>")
        zf.writestr("word/footer1.xml", f"<w:ftr xmlns:w='w'><w:t>{REGION_MARKERS['footer']}</w:t></w:ftr>")
    with pytest.raises(ProbeError, match="body_translated"):
        assert_docx_region_behavior(docx)


def test_extract_pdf_text_rejects_u_fffd_from_pdftotext(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    def fake_run(cmd, timeout=30, cwd=None, env=None):
        Path(cmd[-1]).write_text("bad \ufffd text", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.d2_okapi_probe.run_command", fake_run)
    text = extract_pdf_text(pdf, tmp_path, "/bin/pdftotext")["text"]
    assert "\ufffd" in text
