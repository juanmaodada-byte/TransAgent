"""D4 native DOCX translation orchestration tests."""

import copy
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
import zipfile

import pytest

from transagent.interface import DocumentBlock
from transagent.backend.pipeline.doc_normalizer import resolve_libreoffice
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline import native_document as native
from transagent.backend.pipeline.native_translate import (
    DeterministicFakeTranslator,
    NativeTranslationError,
    RealLLMBlockTranslator,
    sanitize_blocks_for_translator,
    translate_native_docx,
)


def _translated_copy(block: DocumentBlock, text: str | None = None) -> DocumentBlock:
    return DocumentBlock(
        block_id=block.block_id,
        block_type=block.block_type,
        source_text=block.source_text,
        text=text if text is not None else f"译:{block.text}",
        order=block.order,
        metadata=dict(block.metadata),
    )


class SpyTranslator:
    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self.seen_blocks: list[DocumentBlock] = []

    def translate_blocks(self, blocks, source_lang, target_lang):
        self.seen_blocks = blocks
        translated = [_translated_copy(block) for block in blocks]
        if self.mode == "reversed":
            return list(reversed(translated))
        if self.mode == "missing":
            return translated[:-1]
        if self.mode == "duplicate":
            return translated + [translated[0]]
        if self.mode == "unknown":
            translated[0].block_id = "unknown-d4-id"
            return translated
        if self.mode == "raise":
            raise RuntimeError("contains sensitive source text that must not leak")
        return translated


class EmptyMetadataTranslator:
    def __init__(self) -> None:
        self.seen_blocks: list[DocumentBlock] = []

    def translate_blocks(self, blocks, source_lang, target_lang):
        self.seen_blocks = blocks
        translated = []
        for block in blocks:
            assert block.metadata == {
                "placeholder_tokens": list(block.metadata.get("placeholder_tokens", [])),
            }
            translated.append(DocumentBlock(
                block_id=block.block_id,
                block_type=block.block_type,
                source_text=block.source_text,
                text=f"译:{block.text}",
                order=block.order,
                metadata={},
            ))
        return translated


class PlaceholderMutatingTranslator:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def translate_blocks(self, blocks, source_lang, target_lang):
        translated = [_translated_copy(block) for block in blocks]
        if self.mode == "reorder":
            target = next(block for block in translated if len(block.metadata.get("placeholder_tokens", [])) >= 2)
        else:
            target = next(block for block in translated if block.metadata.get("placeholder_tokens"))
        tokens = target.metadata["placeholder_tokens"]
        if self.mode == "delete":
            target.text = target.text.replace(tokens[0], "", 1)
        elif self.mode == "modify":
            target.text = target.text.replace(tokens[0], "[[TA_CHANGED1]]", 1)
        elif self.mode == "reorder":
            target.text = target.text.replace(tokens[0], "__A__", 1).replace(tokens[1], tokens[0], 1).replace("__A__", tokens[1], 1)
        elif self.mode == "duplicate":
            target.text = target.text + tokens[0]
        return translated


def _recursive_repr(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda obj: obj.__dict__)


def _translate_or_skip_cjk_env(*args, **kwargs):
    try:
        return translate_native_docx(*args, **kwargs)
    except NativeTranslationError as exc:
        if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE: CJK font rendering runtime is unavailable"):
            pytest.skip("CJK font rendering runtime unavailable")
        raise


def _metadata_repr(block: DocumentBlock) -> str:
    return _recursive_repr({
        "block_id": block.block_id,
        "block_type": block.block_type,
        "order": block.order,
        "metadata": block.metadata,
    })


def _assert_no_sensitive_translator_metadata(block: DocumentBlock) -> None:
    metadata_text = _metadata_repr(block)
    for forbidden in [
        "<",
        "urn:oasis:names:tc:xliff:document:1.2",
        "word/document.xml",
        ".xlf",
        "/private/internal",
        "placeholder_events",
        "source_inline_signature",
    ]:
        assert forbidden not in metadata_text


def test_sanitize_blocks_for_translator_removes_real_sensitive_metadata():
    block = DocumentBlock(
        block_id="u1",
        block_type="text",
        text="A [[TA_PH1]]",
        metadata={
            "placeholder_tokens": ["[[TA_PH1]]"],
            "placeholder_events": [
                {
                    "kind": "atom",
                    "token": "[[TA_PH1]]",
                    "tag": "{urn:oasis:names:tc:xliff:document:1.2}ph",
                    "attrs": {"id": "1", "ctype": "x-bold"},
                    "xml": '<ns0:ph xmlns:ns0="urn:oasis:names:tc:xliff:document:1.2" id="1">&lt;b&gt;</ns0:ph>',
                }
            ],
            "placeholder_signature": [{"token": "[[TA_PH1]]", "ordinal": 1}],
            "source_inline_signature": [{"tag": "...", "attrs": [("id", "1")]}],
            "xliff_file_original": "word/document.xml",
            "xliff_unit_id": "u1",
            "xliff_path": "/private/internal/source.xlf",
            "work_dir": "/private/internal",
            "okapi_filter_config_id": "okf_openxml@openxml_docx_p0",
        },
    )
    sanitized = sanitize_blocks_for_translator([block])[0]
    assert sanitized.block_id == "u1"
    assert sanitized.text == block.text
    assert sanitized.metadata == {
        "placeholder_tokens": ["[[TA_PH1]]"],
    }
    sanitized_repr = _recursive_repr(sanitized)
    for forbidden in [
        "placeholder_events",
        "placeholder_signature",
        "tag",
        "attrs",
        "xml",
        "xliff",
        "word/document.xml",
        "/private/internal",
        "okf_openxml",
    ]:
        assert forbidden not in sanitized_repr


def test_sanitize_blocks_for_translator_does_not_mutate_or_share_source_metadata():
    original_metadata = {
        "placeholder_tokens": ["[[TA_PH1]]"],
        "placeholder_events": [{"token": "[[TA_PH1]]", "xml": "<ph/>"}],
        "nested": {"unsafe": ["word/document.xml"]},
    }
    block = DocumentBlock(
        block_id="u1",
        block_type="text",
        text="A [[TA_PH1]]",
        metadata=copy.deepcopy(original_metadata),
    )
    original_block = copy.deepcopy(block)

    sanitized = sanitize_blocks_for_translator([block])[0]
    sanitized.text = "changed"
    sanitized.metadata["placeholder_tokens"].append("[[TA_PH2]]")
    sanitized.metadata["new_key"] = ["new"]

    assert block.text == original_block.text
    assert block.metadata["placeholder_tokens"] == original_block.metadata["placeholder_tokens"]
    assert block.metadata["placeholder_events"] == original_block.metadata["placeholder_events"]
    assert block.metadata == original_block.metadata
    assert sanitized.metadata is not block.metadata
    assert sanitized.metadata["placeholder_tokens"] is not block.metadata["placeholder_tokens"]


def test_default_fake_translator_preserves_placeholder_sequence():
    block = DocumentBlock(
        block_id="u1",
        block_type="text",
        text="A [[TA_G1_START]]B[[TA_G1_END]]",
        metadata={"placeholder_tokens": ["[[TA_G1_START]]", "[[TA_G1_END]]"]},
    )
    translated = DeterministicFakeTranslator().translate_blocks([block], "en", "zh-CN")[0]
    assert translated.text == "译:A [[TA_G1_START]]B[[TA_G1_END]]"
    assert translated.metadata == {"placeholder_tokens": ["[[TA_G1_START]]", "[[TA_G1_END]]"]}


def test_real_llm_adapter_is_reserved_and_does_not_run_network():
    with pytest.raises(NativeTranslationError, match="DOCUMENT_TRANSLATION_ERROR"):
        RealLLMBlockTranslator().translate_blocks([], "en", "zh-CN")


@pytest.mark.integration
def test_default_fake_translator_completes_real_docx(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, session_id="d4fake")
    output = Path(result.output_document_path)
    assert output.exists() and output.stat().st_size > 0
    assert output.resolve() != Path(okapi_probe_docx_path).resolve()
    assert result.fidelity_level == "native"
    assert result.summary["block_count"] == len(result.preprocess_result.blocks)
    assert result.summary["placeholder_count"] > 0
    assert len(result.summary["output_sha256"]) == 64
    assert snapshot_docx_structure(str(output)) == result.preprocess_result.original_structure_snapshot


@pytest.mark.integration
def test_translator_only_receives_sanitized_blocks(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    spy = SpyTranslator()
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, translator=spy, session_id="d4spy")
    assert Path(result.output_document_path).exists()
    assert spy.seen_blocks
    for block in spy.seen_blocks:
        assert set(block.metadata) == {"placeholder_tokens"}
        _assert_no_sensitive_translator_metadata(block)
        assert not hasattr(block, "xliff_path")
        assert not hasattr(block, "work_dir")


@pytest.mark.integration
def test_translator_returning_empty_metadata_still_merges_with_server_state(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    translator = EmptyMetadataTranslator()
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, translator=translator, session_id="d4empty")
    output = Path(result.output_document_path)
    assert output.exists() and output.stat().st_size > 0
    assert translator.seen_blocks
    assert all(block.metadata == {} for block in result.translated_blocks)
    assert snapshot_docx_structure(str(output)) == result.preprocess_result.original_structure_snapshot


@pytest.mark.integration
def test_real_docx_loop_translator_metadata_does_not_leak_xml(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    spy = SpyTranslator()
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, translator=spy, session_id="d4noleak")
    assert Path(result.output_document_path).exists()
    assert spy.seen_blocks
    for block in spy.seen_blocks:
        assert set(block.metadata) == {"placeholder_tokens"}
        _assert_no_sensitive_translator_metadata(block)


@pytest.mark.integration
def test_translator_returned_reordered_blocks_are_accepted(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, translator=SpyTranslator("reversed"), session_id="d4reorder")
    assert Path(result.output_document_path).exists()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("missing", "DOCUMENT_TRANSLATION_CONTRACT_ERROR"),
        ("duplicate", "DOCUMENT_TRANSLATION_CONTRACT_ERROR"),
        ("unknown", "DOCUMENT_TRANSLATION_CONTRACT_ERROR"),
    ],
)
def test_id_contract_errors_are_rejected(okapi_probe_docx_path, mode, code):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    with pytest.raises(NativeTranslationError, match=code):
        translate_native_docx(okapi_probe_docx_path, translator=SpyTranslator(mode), session_id=f"d4{mode}")


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["delete", "modify", "reorder", "duplicate"])
def test_placeholder_contract_errors_are_rejected(okapi_probe_docx_path, mode):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    with pytest.raises(NativeTranslationError, match="DOCUMENT_PLACEHOLDER_ERROR"):
        translate_native_docx(
            okapi_probe_docx_path,
            translator=PlaceholderMutatingTranslator(mode),
            session_id=f"d4ph{mode}",
        )


@pytest.mark.integration
def test_translator_exception_maps_to_translation_error_without_sensitive_detail(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    with pytest.raises(NativeTranslationError) as err:
        translate_native_docx(okapi_probe_docx_path, translator=SpyTranslator("raise"), session_id="d4raise")
    message = str(err.value)
    assert message.startswith("DOCUMENT_TRANSLATION_ERROR:")
    assert "sensitive source text" not in message
    assert str(native.TIKAL) not in message


@pytest.mark.integration
def test_output_docx_structure_snapshot_matches_source_skeleton(okapi_probe_docx_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, session_id="d4snapshot")
    assert snapshot_docx_structure(result.output_document_path) == result.preprocess_result.original_structure_snapshot
    with zipfile.ZipFile(result.output_document_path) as zf:
        document_xml = ET.fromstring(zf.read("word/document.xml"))
    assert "".join(document_xml.itertext())


@pytest.mark.integration
def test_real_docx_loop_renders_non_empty_pdf_when_libreoffice_available(okapi_probe_docx_path, tmp_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    try:
        soffice = resolve_libreoffice().executable
    except ValueError:
        pytest.skip("LibreOffice runtime unavailable")
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, session_id="d4render")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    completed = native.subprocess.run(
        [str(soffice), "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), result.output_document_path],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0
    pdf = pdf_dir / f"{Path(result.output_document_path).stem}.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0


@pytest.mark.integration
def test_empty_metadata_real_docx_loop_preserves_structure_and_images(okapi_probe_docx_path, tmp_path):
    if not native.TIKAL.exists():
        pytest.skip("Okapi runtime unavailable")
    try:
        soffice = resolve_libreoffice().executable
    except ValueError:
        pytest.skip("LibreOffice runtime unavailable")

    before = snapshot_docx_structure(okapi_probe_docx_path)
    translator = EmptyMetadataTranslator()
    result = _translate_or_skip_cjk_env(okapi_probe_docx_path, translator=translator, session_id="d4emptyloop")
    output = Path(result.output_document_path)
    after = snapshot_docx_structure(str(output))

    assert output.exists() and output.stat().st_size > 0
    assert output.resolve() != Path(okapi_probe_docx_path).resolve()
    assert before["images"] == after["images"]
    assert before["table_count"] == after["table_count"]
    assert before["section_count"] == after["section_count"]
    assert before["header_footer_relationships"] == after["header_footer_relationships"]
    with zipfile.ZipFile(output) as zf:
        xml_text = "\n".join(
            zf.read(name).decode("utf-8", errors="ignore")
            for name in zf.namelist()
            if name.endswith(".xml")
        )
    assert not re.search(r"\[\[TA_[A-Z0-9]+(?:_START|_END)?\]\]", xml_text)

    pdf_dir = tmp_path / "pdf-empty-metadata"
    pdf_dir.mkdir()
    completed = native.subprocess.run(
        [str(soffice), "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(output)],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0
    pdf = pdf_dir / f"{output.stem}.pdf"
    assert pdf.exists() and pdf.stat().st_size > 0
