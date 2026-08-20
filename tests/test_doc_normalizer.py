from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from transagent.interface import FormatType
from transagent.backend.pipeline import doc_normalizer as norm
from transagent.backend.pipeline import native_document as native


def _doc_fmt():
    return SimpleNamespace(format_type=FormatType.DOC.value)


def test_build_convert_command_uses_fixed_array_profile_and_outdir(tmp_path):
    cmd = norm.build_convert_command(
        tmp_path / "soffice",
        tmp_path / "source.doc",
        tmp_path / "out",
        tmp_path / "profile",
    )
    assert isinstance(cmd, list)
    assert cmd[0].endswith("soffice")
    assert cmd[1].startswith("-env:UserInstallation=file://")
    assert "--headless" in cmd
    assert cmd[cmd.index("--convert-to") + 1] == "docx"
    assert cmd[cmd.index("--outdir") + 1] == str((tmp_path / "out").resolve())
    assert cmd[-1] == str((tmp_path / "source.doc").resolve())


def test_convert_uses_subprocess_array_no_shell_independent_profile_timeout(monkeypatch, tmp_path):
    source = tmp_path / "source.doc"
    source.write_bytes(b"doc")
    captured = {}

    monkeypatch.setattr(norm, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(
        norm,
        "resolve_libreoffice",
        lambda: norm.LibreOfficeInfo(tmp_path / "soffice", "LibreOffice test"),
    )
    monkeypatch.setattr(norm, "validate_normalized_docx", lambda path: None)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = norm.convert_doc_to_docx(source, work_dir)
    assert output.name == "source.docx"
    assert output.parent == work_dir.resolve()
    assert "-env:UserInstallation=" in captured["cmd"][1]
    assert "libreoffice-profile" in captured["cmd"][1]
    assert captured["kwargs"]["timeout"] == norm.CONVERT_TIMEOUT_SECONDS
    assert "shell" not in captured["kwargs"]
    assert source.exists() and source.read_bytes() == b"doc"


def test_libreoffice_missing_returns_stable_error(monkeypatch, tmp_path):
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(norm, "PROJECT_SOFFICE_CANDIDATES", (tmp_path / "missing-soffice",))
    monkeypatch.setattr(norm.shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        norm.resolve_libreoffice()


def test_configured_libreoffice_invalid_fails_closed(monkeypatch, tmp_path):
    fallback = tmp_path / "soffice"
    fallback.write_text("#!/bin/sh\nprintf 'LibreOffice test\\n'\n", encoding="utf-8")
    fallback.chmod(0o755)
    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path / "missing-soffice"))
    monkeypatch.setattr(norm, "PROJECT_SOFFICE_CANDIDATES", (fallback,))
    monkeypatch.setattr(norm.shutil, "which", lambda name: str(fallback))
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        norm.resolve_libreoffice()


def test_libreoffice_does_not_reference_codex_cache_path():
    source = Path("backend/pipeline/doc_normalizer.py").read_text(encoding="utf-8")
    assert ".cache/codex-runtimes" not in source


@pytest.mark.parametrize(
    ("completed", "match"),
    [
        (
            SimpleNamespace(returncode=1, stdout="sensitive diagnostic", stderr="sensitive diagnostic"),
            "DOCUMENT_CONVERSION_ERROR",
        ),
    ],
)
def test_nonzero_exit_returns_conversion_error(monkeypatch, tmp_path, completed, match):
    source = tmp_path / "source.doc"
    original_bytes = b"doc"
    source.write_bytes(original_bytes)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    called = 0

    monkeypatch.setattr(norm, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(
        norm,
        "resolve_libreoffice",
        lambda: norm.LibreOfficeInfo(tmp_path / "soffice", "LibreOffice test"),
    )

    def fake_run(cmd, **kwargs):
        nonlocal called
        called += 1
        return completed

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match=match) as err:
        norm.convert_doc_to_docx(source, work_dir)
    assert called == 1
    assert "sensitive diagnostic" not in str(err.value)
    assert source.read_bytes() == original_bytes


def test_timeout_returns_conversion_error_without_leaking_details(monkeypatch, tmp_path):
    source = tmp_path / "source.doc"
    original_bytes = b"doc"
    source.write_bytes(original_bytes)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    called = 0

    monkeypatch.setattr(norm, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(
        norm,
        "resolve_libreoffice",
        lambda: norm.LibreOfficeInfo(tmp_path / "secret-soffice", "LibreOffice test"),
    )

    def fake_run(cmd, **kwargs):
        nonlocal called
        called += 1
        raise subprocess.TimeoutExpired(
            cmd=["secret-soffice", "--convert-to", "docx"],
            timeout=1,
            output="sensitive diagnostic",
            stderr="sensitive diagnostic",
        )

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_CONVERSION_ERROR") as err:
        norm.convert_doc_to_docx(source, work_dir)
    assert called == 1
    assert "secret-soffice" not in str(err.value)
    assert "sensitive diagnostic" not in str(err.value)
    assert source.read_bytes() == original_bytes


def test_success_without_output_is_rejected_through_normalizer(monkeypatch, tmp_path):
    source = tmp_path / "source.doc"
    original_bytes = b"doc"
    source.write_bytes(original_bytes)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    called = 0

    monkeypatch.setattr(norm, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(
        norm,
        "resolve_libreoffice",
        lambda: norm.LibreOfficeInfo(tmp_path / "soffice", "LibreOffice test"),
    )

    def fake_run(cmd, **kwargs):
        nonlocal called
        called += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR") as err:
        norm.normalize_doc_to_docx(source, work_dir)
    assert called == 1
    assert "did not create DOCX output" in str(err.value)
    assert source.read_bytes() == original_bytes


def test_success_with_empty_output_is_rejected_through_normalizer(monkeypatch, tmp_path):
    source = tmp_path / "source.doc"
    original_bytes = b"doc"
    source.write_bytes(original_bytes)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    called = 0

    monkeypatch.setattr(norm, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(
        norm,
        "resolve_libreoffice",
        lambda: norm.LibreOfficeInfo(tmp_path / "soffice", "LibreOffice test"),
    )

    def fake_run(cmd, **kwargs):
        nonlocal called
        called += 1
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        outdir.joinpath("source.docx").write_bytes(b"")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(norm.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR") as err:
        norm.normalize_doc_to_docx(source, work_dir)
    assert called == 1
    assert "normalized DOCX is empty" in str(err.value)
    assert source.read_bytes() == original_bytes


def test_no_output_empty_output_and_non_docx_are_rejected(tmp_path):
    missing = tmp_path / "missing.docx"
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        norm.validate_normalized_docx(missing)

    empty = tmp_path / "empty.docx"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        norm.validate_normalized_docx(empty)

    invalid = tmp_path / "invalid.docx"
    invalid.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR|DOCUMENT_FORMAT_MISMATCH"):
        norm.validate_normalized_docx(invalid)


def test_session_id_path_traversal_is_rejected_before_doc_conversion(monkeypatch, tmp_path):
    called = False

    def fake_normalize(source, work_dir):
        nonlocal called
        called = True

    monkeypatch.setattr(native, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(native, "normalize_doc_to_docx", fake_normalize)
    with pytest.raises(ValueError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        native.extract_document(str(tmp_path / "source.doc"), session_id="../escape")
    assert called is False


def test_pdf_and_other_formats_do_not_enter_doc_conversion(monkeypatch, tmp_path):
    called = False

    def fake_normalize(source, work_dir):
        nonlocal called
        called = True

    monkeypatch.setattr(native, "normalize_doc_to_docx", fake_normalize)
    monkeypatch.setattr(native, "detect_format", lambda path: SimpleNamespace(format_type=FormatType.PDF.value))
    monkeypatch.setattr(native, "normalize_pdf_to_docx", lambda *args: (_ for _ in ()).throw(ValueError("DOCUMENT_OCR_UNSUPPORTED: PDF has no extractable text layer")))
    with pytest.raises(ValueError, match="DOCUMENT_OCR_UNSUPPORTED"):
        native.extract_document(str(tmp_path / "source.pdf"), session_id="safe")
    assert called is False


def test_docx_native_route_does_not_call_normalizer(monkeypatch, tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(b"docx")
    monkeypatch.setattr(native, "detect_format", lambda path: SimpleNamespace(format_type=FormatType.DOCX.value))
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot_docx_structure", lambda path: {})
    monkeypatch.setattr(native, "_validate_xliff_file", lambda path: None)
    monkeypatch.setattr(native, "_blocks_from_xliff", lambda path: [SimpleNamespace(block_id="u1", text="A", block_type="text")])
    monkeypatch.setattr(native, "normalize_doc_to_docx", lambda *args: pytest.fail("DOCX route called normalizer"))

    def fake_run(cmd, cwd, timeout, error_code):
        Path(cmd[cmd.index("-od") + 1], f"{Path(cmd[-1]).name}.xlf").write_text("<x/>", encoding="utf-8")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "_run_tikal", fake_run)
    result = native.extract_document(str(source), session_id="safe")
    assert result.fidelity_level == "native"


def test_doc_route_converts_once(monkeypatch, tmp_path):
    source = tmp_path / "source.doc"
    source.write_bytes(b"doc")
    converted = tmp_path / "work" / "source.docx"
    calls = []

    monkeypatch.setattr(native, "detect_format", lambda path: _doc_fmt())
    monkeypatch.setattr(native, "_ensure_runtime", lambda: None)
    monkeypatch.setattr(native, "snapshot_docx_structure", lambda path: {})
    monkeypatch.setattr(native, "_validate_xliff_file", lambda path: None)
    monkeypatch.setattr(native, "_blocks_from_xliff", lambda path: [SimpleNamespace(block_id="u1", text="A", block_type="text")])
    monkeypatch.setattr(native, "_create_work_dir", lambda session_id: tmp_path / "work")

    def fake_normalize(src, work_dir):
        calls.append((src, work_dir))
        converted.parent.mkdir(parents=True)
        converted.write_bytes(b"docx")
        return norm.NormalizedDocx(converted, tmp_path / "soffice", "LibreOffice test", ["warn"])

    def fake_run(cmd, cwd, timeout, error_code):
        Path(cmd[cmd.index("-od") + 1], f"{Path(cmd[-1]).name}.xlf").write_text("<x/>", encoding="utf-8")
        return native.CommandResult(0, "", "")

    monkeypatch.setattr(native, "normalize_doc_to_docx", fake_normalize)
    monkeypatch.setattr(native, "_run_tikal", fake_run)
    result = native.extract_document(str(source), session_id="safe")
    assert len(calls) == 1
    assert result.fidelity_level == "normalized"
    assert result.document_manifest.normalized_from_doc is True
