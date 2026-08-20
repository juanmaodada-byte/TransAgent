"""D4 DOCX-native translation orchestration layer."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Protocol

from transagent.interface import DocumentBlock, NativeTranslationResult
from transagent.backend.pipeline import native_document as native


TRANSLATION_ERROR_CODES = (
    "DOCUMENT_TRANSLATION_CONTRACT_ERROR",
    "DOCUMENT_PLACEHOLDER_ERROR",
    "DOCUMENT_TRANSLATION_ERROR",
    "DOCUMENT_MERGE_ERROR",
    "DOCUMENT_INTEGRITY_ERROR",
)
SAFE_PLACEHOLDER_KEYS = {
    "placeholder_tokens",
}
PLACEHOLDER_RE = re.compile(r"\[\[TA_[A-Z0-9]+(?:_START|_END)?\]\]")


class BlockTranslator(Protocol):
    def translate_blocks(
        self,
        blocks: list[DocumentBlock],
        source_lang: str,
        target_lang: str,
    ) -> list[DocumentBlock]:
        ...


class NativeTranslationError(ValueError):
    """D4 native translation error with a stable DOCUMENT_* prefix."""


class RealLLMBlockTranslator:
    """Reserved adapter boundary for a future real LLM implementation."""

    def translate_blocks(
        self,
        blocks: list[DocumentBlock],
        source_lang: str,
        target_lang: str,
    ) -> list[DocumentBlock]:
        raise NativeTranslationError(
            "DOCUMENT_TRANSLATION_ERROR: real LLM adapter is not implemented in D4"
        )


class DeterministicFakeTranslator:
    """Deterministic local translator for D4 tests and offline demos."""

    def __init__(self, prefix: str = "译:") -> None:
        self.prefix = prefix

    def translate_blocks(
        self,
        blocks: list[DocumentBlock],
        source_lang: str,
        target_lang: str,
    ) -> list[DocumentBlock]:
        return [
            DocumentBlock(
                block_id=block.block_id,
                block_type=block.block_type,
                source_text=block.source_text,
                text=_prefix_visible_text(block.text, self.prefix),
                order=block.order,
                metadata={
                    "placeholder_tokens": list(block.metadata.get("placeholder_tokens", [])),
                },
            )
            for block in blocks
        ]


def translate_native_docx(
    file_path: str,
    translator: BlockTranslator | None = None,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    session_id: str | None = None,
) -> NativeTranslationResult:
    """Translate a DOCX through the native extract -> block translator -> merge loop."""
    active_translator = translator or DeterministicFakeTranslator()
    try:
        preprocess_result = native.extract_document(
            file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            session_id=session_id,
        )
        sanitized_blocks = sanitize_blocks_for_translator(preprocess_result.blocks)
        try:
            translated_blocks = active_translator.translate_blocks(
                sanitized_blocks,
                source_lang,
                target_lang,
            )
        except Exception as exc:
            raise NativeTranslationError(
                "DOCUMENT_TRANSLATION_ERROR: translator failed before producing valid blocks"
            ) from exc
        if translated_blocks is None:
            raise NativeTranslationError("DOCUMENT_TRANSLATION_ERROR: translator returned no blocks")

        source_by_id = {block.block_id: block for block in preprocess_result.blocks}
        native._validate_translated_blocks(translated_blocks, source_by_id)
        output_path = native.merge_translations(preprocess_result, translated_blocks, session_id=session_id)
        summary = _summary(preprocess_result.blocks, translated_blocks, output_path)
        return NativeTranslationResult(
            source_document_path=file_path,
            output_document_path=output_path,
            preprocess_result=preprocess_result,
            translated_blocks=translated_blocks,
            source_lang=source_lang,
            target_lang=target_lang,
            session_id=session_id or "",
            extraction_id=preprocess_result.extraction_id,
            fidelity_level="native",
            warnings=list(preprocess_result.conversion_warnings),
            summary=summary,
        )
    except (NativeTranslationError, native.NativeDocumentError, ValueError) as exc:
        raise _normalize_error(exc) from exc


def sanitize_blocks_for_translator(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Return the minimal block view allowed across the translator boundary."""
    sanitized: list[DocumentBlock] = []
    for block in blocks:
        safe_metadata = {
            "placeholder_tokens": list(block.metadata.get("placeholder_tokens", [])),
        }
        sanitized.append(DocumentBlock(
            block_id=block.block_id,
            block_type=block.block_type,
            source_text=block.source_text,
            text=block.text,
            order=block.order,
            metadata=safe_metadata,
        ))
    return sanitized


def _summary(
    source_blocks: list[DocumentBlock],
    translated_blocks: list[DocumentBlock],
    output_path: str,
) -> dict:
    return {
        "block_count": len(source_blocks),
        "source_character_count": sum(len(_block_text(block)) for block in source_blocks),
        "translated_character_count": sum(len(_block_text(block)) for block in translated_blocks),
        "placeholder_count": sum(len(block.metadata.get("placeholder_tokens", [])) for block in source_blocks),
        "output_sha256": _sha256_file(Path(output_path)),
    }


def _normalize_error(exc: Exception) -> NativeTranslationError:
    message = str(exc)
    for code in TRANSLATION_ERROR_CODES:
        if message.startswith(f"{code}:"):
            return NativeTranslationError(message)
    if message.startswith("DOCUMENT_EXTRACTION_ERROR:") or message.startswith("DOCUMENT_RUNTIME_UNAVAILABLE:"):
        return NativeTranslationError(message)
    return NativeTranslationError("DOCUMENT_TRANSLATION_ERROR: native translation failed")


def _prefix_visible_text(text: str, prefix: str) -> str:
    if not text:
        return prefix
    match = PLACEHOLDER_RE.search(text)
    if match and match.start() == 0:
        return text[:match.end()] + prefix + text[match.end():]
    return prefix + text


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _block_text(block: DocumentBlock) -> str:
    if getattr(block, "text", ""):
        return block.text
    if getattr(block, "source_text", ""):
        return block.source_text
    return getattr(block, "target_text", "")
