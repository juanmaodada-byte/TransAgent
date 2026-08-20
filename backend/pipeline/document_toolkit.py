"""Two-tool document interface for clean embedding into the host tool.

This is a thin layer over :mod:`native_document` that splits the document
pipeline into two independently callable tools so the host tool can embed them
as "pre-translation" and "post-translation" steps without ever touching the
module's internal artifacts (XLIFF, skeleton, structure snapshot, server-side
block metadata, work directory).

The handoff between the two tools is a :class:`DocumentHandle` (a plain
identifier + sanitized translation view), NOT the raw ``PreprocessResult``. The
full server-side state is held by a :class:`DocumentArtifactStore` keyed by
``document_id``.

Usage (synchronous, single process):

    from transagent.backend.pipeline.document_toolkit import (
        prepare_document,
        finalize_document,
    )

    handle = prepare_document("input.docx", source_lang="en", target_lang="zh-CN")
    translated_blocks = my_translator.translate_blocks(handle.blocks, ...)
    output_path = finalize_document(handle, translated_blocks)

Usage (async / cross-process — share one store root):

    store = DocumentArtifactStore(root="/shared/document-artifacts")
    handle = prepare_document("input.docx", store=store)
    # ... enqueue handle.document_id, translate elsewhere ...
    output_path = finalize_document(handle, translated_blocks, store=store)
"""

from __future__ import annotations

import pickle
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from transagent.interface import DocumentBlock, PreprocessResult
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline.native_translate import sanitize_blocks_for_translator


class DocumentToolkitError(ValueError):
    """Document toolkit error with a stable DOCUMENT_* prefix."""


@dataclass
class DocumentHandle:
    """Handoff handle between the pre- and post-translation tools.

    Contains only data the host tool (and its translation module) may see. It
    deliberately carries NO internal paths or server-side restore state:
    ``work_dir``, ``xliff_path``, ``skeleton_path``, the structure snapshot,
    and per-block server metadata all stay in the artifact store.
    """

    document_id: str
    extraction_id: str
    fidelity_level: str
    source_lang: str
    target_lang: str
    blocks: list  # list[DocumentBlock] — sanitized view for the translator
    protected_md: str
    conversion_warnings: list = field(default_factory=list)


class DocumentArtifactStore:
    """Server-side state holder keyed by ``document_id``.

    In-memory first (fast for a single-process host), with optional disk
    persistence when ``root`` is given (enables a cross-process / multi-worker
    host). ``load`` falls back to disk when the id is not cached in memory, so a
    worker that only calls ``finalize_document`` can still retrieve the artifact
    produced by a different process that called ``prepare_document``.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else None
        self._cache: dict[str, PreprocessResult] = {}

    def _artifact_path(self, document_id: str) -> Path:
        return self._root / document_id / "artifact.pkl"  # type: ignore[operator]

    def save(self, handle: DocumentHandle, result: PreprocessResult) -> None:
        self._cache[handle.document_id] = result
        if self._root is not None:
            path = self._artifact_path(handle.document_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                pickle.dump(result, fh)
            tmp.replace(path)

    def load(self, handle: DocumentHandle) -> PreprocessResult:
        cached = self._cache.get(handle.document_id)
        if cached is not None:
            return cached
        if self._root is not None:
            path = self._artifact_path(handle.document_id)
            if path.exists():
                with path.open("rb") as fh:
                    result = pickle.load(fh)
                self._cache[handle.document_id] = result
                return result
        raise DocumentToolkitError(
            "DOCUMENT_TRANSLATION_CONTRACT_ERROR: "
            f"unknown document_id {handle.document_id!r} (not prepared in this store)"
        )

    def delete(self, handle: DocumentHandle) -> None:
        self._cache.pop(handle.document_id, None)
        if self._root is not None:
            shutil.rmtree(self._root / handle.document_id, ignore_errors=True)


_DEFAULT_STORE: DocumentArtifactStore | None = None


def _default_store() -> DocumentArtifactStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = DocumentArtifactStore()
    return _DEFAULT_STORE


def prepare_document(
    file_path: str,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    session_id: str | None = None,
    store: DocumentArtifactStore | None = None,
) -> DocumentHandle:
    """Tool A — pre-translation: file -> handoff handle.

    Extracts the document into blocks, sanitizes them into the translator view,
    stores the full server-side state in ``store``, and returns only the handle.
    """
    result = native.extract_document(
        file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        session_id=session_id,
    )
    manifest = result.document_manifest
    if manifest is None or not manifest.document_id:
        raise DocumentToolkitError(
            "DOCUMENT_EXTRACTION_ERROR: extraction produced no document manifest"
        )
    handle = DocumentHandle(
        document_id=manifest.document_id,
        extraction_id=manifest.extraction_id,
        fidelity_level=result.fidelity_level,
        source_lang=result.source_lang,
        target_lang=result.target_lang,
        blocks=sanitize_blocks_for_translator(result.blocks),
        protected_md=result.protected_md,
        conversion_warnings=list(result.conversion_warnings),
    )
    (store or _default_store()).save(handle, result)
    return handle


def finalize_document(
    handle: DocumentHandle,
    translated_blocks: list[DocumentBlock],
    session_id: str | None = None,
    store: DocumentArtifactStore | None = None,
) -> str:
    """Tool B — post-translation: handle + translated blocks -> final DOCX path.

    Retrieves the full server-side state (XLIFF, skeleton, snapshot, server
    metadata) from ``store`` and runs the standard merge + delivery gates.
    """
    result = (store or _default_store()).load(handle)
    return native.merge_translations(result, translated_blocks, session_id=session_id)
