"""Document toolkit (prepare / finalize) tests.

Splits into two groups:
- Unit tests (no runtime): handle shape + artifact store round-trips.
- Integration tests: prepare_document -> finalize_document, skipped when the
  Okapi/Tikal runtime is unavailable (same convention as test_native_translate).
"""

from __future__ import annotations

import pytest

from transagent.interface import (
    DocumentArtifactManifest,
    DocumentBlock,
    PreprocessResult,
)
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline.document_toolkit import (
    DocumentArtifactStore,
    DocumentHandle,
    DocumentToolkitError,
    finalize_document,
    prepare_document,
)
from transagent.backend.pipeline.native_translate import DeterministicFakeTranslator


def _make_result(document_id: str = "doc-1") -> PreprocessResult:
    manifest = DocumentArtifactManifest(
        document_id=document_id,
        extraction_id="ext-1",
        source_format="docx",
        fidelity_level="native",
        source_lang="en",
        target_lang="zh-CN",
    )
    block = DocumentBlock(
        block_id="u1",
        block_type="text",
        text="Hello [[TA_PH1]]",
        metadata={
            "placeholder_tokens": ["[[TA_PH1]]"],
            "placeholder_events": [{"token": "[[TA_PH1]]", "xml": "<ph/>"}],
            "source_inline_signature": [{"tag": "ph"}],
        },
    )
    return PreprocessResult(
        blocks=[block],
        protected_md="# Hello",
        document_manifest=manifest,
        fidelity_level="native",
        source_lang="en",
        target_lang="zh-CN",
    )


def _make_handle(document_id: str = "doc-1") -> DocumentHandle:
    return DocumentHandle(
        document_id=document_id,
        extraction_id="ext-1",
        fidelity_level="native",
        source_lang="en",
        target_lang="zh-CN",
        blocks=[],
        protected_md="",
    )


# ── Unit: handle shape ────────────────────────────────────────────────────


def test_handle_carries_no_internal_paths():
    handle = _make_handle()
    internal_fields = [
        "work_dir",
        "xliff_path",
        "skeleton_path",
        "normalized_docx_path",
        "source_path",
        "document_manifest",
        "original_structure_snapshot",
    ]
    for name in internal_fields:
        assert not hasattr(handle, name), f"handle leaks internal field {name!r}"


# ── Unit: artifact store ───────────────────────────────────────────────────


def test_store_memory_roundtrip_returns_same_result():
    store = DocumentArtifactStore()
    result = _make_result()
    handle = _make_handle()
    store.save(handle, result)
    assert store.load(handle) is result


def test_store_disk_roundtrip_preserves_server_metadata(tmp_path):
    store = DocumentArtifactStore(root=tmp_path)
    result = _make_result()
    handle = _make_handle()
    store.save(handle, result)

    # Simulate a different process: a fresh store has no memory cache and must
    # read from disk.
    reloaded = DocumentArtifactStore(root=tmp_path).load(handle)
    assert reloaded is not result  # disk round-trip produces a fresh object
    assert reloaded.document_manifest.document_id == result.document_manifest.document_id
    assert reloaded.protected_md == result.protected_md
    block = reloaded.blocks[0]
    assert block.block_id == "u1"
    # server-side restore state must survive the round-trip
    assert block.metadata["placeholder_events"] == [{"token": "[[TA_PH1]]", "xml": "<ph/>"}]
    assert block.metadata["source_inline_signature"] == [{"tag": "ph"}]


def test_store_load_unknown_raises_contract_error():
    store = DocumentArtifactStore()
    with pytest.raises(DocumentToolkitError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        store.load(_make_handle("never-prepared"))


def test_store_delete_removes_artifact(tmp_path):
    store = DocumentArtifactStore(root=tmp_path)
    handle = _make_handle()
    store.save(handle, _make_result())
    store.delete(handle)
    with pytest.raises(DocumentToolkitError, match="DOCUMENT_TRANSLATION_CONTRACT_ERROR"):
        store.load(handle)


# ── Integration: prepare / finalize ────────────────────────────────────────


@pytest.mark.integration
def test_prepare_document_returns_sanitized_handle(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    store = DocumentArtifactStore()
    handle = prepare_document(okapi_probe_docx_path, session_id="tkprepare", store=store)
    assert handle.document_id
    assert handle.fidelity_level == "native"
    assert handle.blocks
    for block in handle.blocks:
        assert set(block.metadata) == {"placeholder_tokens"}
    for name in ["work_dir", "xliff_path", "skeleton_path"]:
        assert not hasattr(handle, name)


@pytest.mark.integration
def test_prepare_then_finalize_roundtrip(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    from transagent.backend.pipeline.doc_normalizer import resolve_libreoffice

    try:
        resolve_libreoffice()
    except Exception:
        pytest.skip("LibreOffice runtime unavailable")
    store = DocumentArtifactStore()
    handle = prepare_document(okapi_probe_docx_path, session_id="tkround", store=store)

    # Use an ASCII-only prefix so the round-trip is not blocked by the CJK font
    # gate (an environment concern, orthogonal to the prepare/finalize split).
    translated = DeterministicFakeTranslator(prefix="[TR]").translate_blocks(
        handle.blocks, handle.source_lang, handle.target_lang
    )

    try:
        output_path = finalize_document(handle, translated, session_id="tkround", store=store)
    except ValueError as exc:
        if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE: CJK font rendering runtime is unavailable"):
            pytest.skip("CJK font rendering runtime unavailable")
        raise
    assert output_path
    assert output_path != okapi_probe_docx_path
