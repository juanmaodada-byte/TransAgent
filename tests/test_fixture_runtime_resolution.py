"""LibreOffice runtime resolution tests for fixture generation."""

from __future__ import annotations

import hashlib
import importlib.util
import os

from tests.conftest import FIXTURES_DIR


def _load_fixture_generator():
    script = os.path.join(FIXTURES_DIR, "generate_docx_fixture.py")
    spec = importlib.util.spec_from_file_location("generate_docx_fixture", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_executable(path):
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_fixture_soffice_uses_configured_path_first(monkeypatch, tmp_path):
    generator = _load_fixture_generator()
    configured = _make_executable(tmp_path / "configured-soffice")
    bundled = _make_executable(tmp_path / "bundled-soffice")
    path_soffice = _make_executable(tmp_path / "path-soffice")

    monkeypatch.setenv("SOFFICE_PATH", configured)
    monkeypatch.setattr(generator, "BUNDLED_SOFFICE", bundled)
    monkeypatch.setattr(generator.shutil, "which", lambda name: path_soffice)

    assert generator.resolve_fixture_soffice() == configured


def test_fixture_soffice_falls_back_to_bundled_when_configured_missing(monkeypatch, tmp_path):
    generator = _load_fixture_generator()
    bundled = _make_executable(tmp_path / "bundled-soffice")
    path_soffice = _make_executable(tmp_path / "path-soffice")

    monkeypatch.setenv("SOFFICE_PATH", str(tmp_path / "missing-configured"))
    monkeypatch.setattr(generator, "BUNDLED_SOFFICE", bundled)
    monkeypatch.setattr(generator.shutil, "which", lambda name: path_soffice)

    assert generator.resolve_fixture_soffice() == bundled


def test_fixture_soffice_falls_back_to_path_when_bundled_missing(monkeypatch, tmp_path):
    generator = _load_fixture_generator()
    path_soffice = _make_executable(tmp_path / "path-soffice")

    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(generator, "BUNDLED_SOFFICE", str(tmp_path / "missing-bundled"))
    monkeypatch.setattr(generator.shutil, "which", lambda name: path_soffice)

    assert generator.resolve_fixture_soffice() == path_soffice


def test_fixture_soffice_skips_non_executable_candidates(monkeypatch, tmp_path):
    generator = _load_fixture_generator()
    configured = tmp_path / "configured-soffice"
    configured.write_text("#!/bin/sh\n", encoding="utf-8")
    configured.chmod(0o644)
    bundled = tmp_path / "bundled-soffice"
    bundled.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled.chmod(0o644)
    path_soffice = _make_executable(tmp_path / "path-soffice")

    monkeypatch.setenv("SOFFICE_PATH", str(configured))
    monkeypatch.setattr(generator, "BUNDLED_SOFFICE", str(bundled))
    monkeypatch.setattr(generator.shutil, "which", lambda name: path_soffice)

    assert generator.resolve_fixture_soffice() == path_soffice


def test_fixture_soffice_returns_none_when_all_candidates_unavailable(monkeypatch, tmp_path):
    generator = _load_fixture_generator()
    configured = tmp_path / "configured-soffice"
    configured.write_text("#!/bin/sh\n", encoding="utf-8")
    configured.chmod(0o644)

    monkeypatch.setenv("SOFFICE_PATH", str(configured))
    monkeypatch.setattr(generator, "BUNDLED_SOFFICE", str(tmp_path / "missing-bundled"))
    monkeypatch.setattr(generator.shutil, "which", lambda name: None)

    assert generator.resolve_fixture_soffice() is None


def test_real_doc_fixture_skips_when_soffice_unavailable(monkeypatch, capsys):
    generator = _load_fixture_generator()
    output_doc = os.path.join(FIXTURES_DIR, "real_word_97_mixed.doc")
    before = _sha256(output_doc) if os.path.exists(output_doc) else None

    monkeypatch.setattr(generator, "resolve_fixture_soffice", lambda: None)
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LibreOffice should not run")),
    )

    generator.build_real_doc_fixture()

    captured = capsys.readouterr()
    assert "Skipped real DOC fixture" in captured.out
    after = _sha256(output_doc) if os.path.exists(output_doc) else None
    assert after == before
