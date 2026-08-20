"""Production DOCX-native extraction and merge entry points for D3."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.interface import DocumentArtifactManifest, DocumentBlock, FormatType, PreprocessResult
from transagent.backend.pipeline.document_quality import validate_delivery_docx
from transagent.backend.pipeline.doc_normalizer import normalize_doc_to_docx
from transagent.backend.pipeline.docx_cjk_fonts import apply_cjk_fonts
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline.pdf_normalizer import normalize_pdf_to_docx
from transagent.backend.pipeline.preprocess import detect_format
from transagent.backend.pipeline.xliff_codec import (
    XLIFF_NS,
    assert_placeholder_contract,
    encode_source,
    inline_signature,
    namespace_of,
    qname,
    restore_target,
)


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "runs" / "d3_native_document"
CONFIG_SOURCE = ROOT / "configs" / "okapi" / "openxml_docx_p0.fprm"
CONFIG_ID = "okf_openxml@openxml_docx_p0"
CONFIG_RUN_BASENAME = f"{CONFIG_ID}.fprm"
TIKAL = Path(os.environ.get("TIKAL_PATH", ROOT / ".runtime" / "okapi-1.48.0" / "tikal-java17.sh")).expanduser()
MAX_XLIFF_BYTES = 50 * 1024 * 1024
MAX_OUTPUT_DOCX_BYTES = 200 * 1024 * 1024
EXTRACT_TIMEOUT_SECONDS = 180
MERGE_TIMEOUT_SECONDS = 180
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class NativeDocumentError(ValueError):
    """Native document pipeline error with a stable DOCUMENT_* prefix."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def doc_error(code: str, detail: str) -> NativeDocumentError:
    return NativeDocumentError(f"{code}: {detail}")


def extract_document(
    file_path: str,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
    session_id: str | None = None,
) -> PreprocessResult:
    fmt = detect_format(file_path)
    source = Path(file_path)
    work_dir = _create_work_dir(session_id)

    libreoffice_version = ""
    normalized_from_doc = False
    conversion_warnings: list[str] = []
    conversion_metadata: dict = {}
    libreoffice_executable_path = ""
    if fmt.format_type == FormatType.DOCX.value:
        source_sha = _sha256_file(source)
        normalized_docx = work_dir / _safe_docx_name(source.name)
        shutil.copy2(source, normalized_docx)
        source_format = FormatType.DOCX.value
        fidelity_level = "native"
        normalized_docx_sha = _sha256_file(normalized_docx)
    elif fmt.format_type == FormatType.DOC.value:
        source_sha = _sha256_file(source)
        normalized = normalize_doc_to_docx(source, work_dir)
        normalized_docx = normalized.path
        source_format = FormatType.DOC.value
        fidelity_level = "normalized"
        normalized_from_doc = True
        conversion_warnings = list(normalized.warnings)
        libreoffice_executable_path = str(normalized.libreoffice_executable)
        libreoffice_version = normalized.libreoffice_version
        normalized_docx_sha = _sha256_file(normalized_docx)
    elif fmt.format_type == FormatType.PDF.value:
        normalized_pdf = normalize_pdf_to_docx(source, work_dir)
        source_sha = normalized_pdf.source_sha256
        normalized_docx = normalized_pdf.path
        source_format = FormatType.PDF.value
        fidelity_level = "approximate"
        conversion_warnings = list(normalized_pdf.warnings)
        normalized_docx_sha = normalized_pdf.normalized_docx_sha256
        conversion_metadata = {
            "engine": normalized_pdf.engine,
            "fallback_used": normalized_pdf.fallback_used,
            "source_page_count": normalized_pdf.page_count,
            "text_pages": list(normalized_pdf.text_pages),
            "no_text_pages": list(normalized_pdf.no_text_pages),
            "total_text_chars": normalized_pdf.total_text_chars,
            "runtime": dict(normalized_pdf.runtime),
            "readability": dict(normalized_pdf.readability),
        }
    else:
        raise doc_error("DOCUMENT_UNSUPPORTED_FORMAT", f"{fmt.format_type} is outside current native document scope")

    _ensure_runtime()
    shutil.copy2(CONFIG_SOURCE, work_dir / CONFIG_RUN_BASENAME)
    config_sha = _sha256_file(CONFIG_SOURCE)
    original_snapshot = snapshot_docx_structure(str(normalized_docx))

    result = _run_tikal([
        str(TIKAL),
        "-x",
        "-fc",
        CONFIG_ID,
        "-sl",
        source_lang,
        "-tl",
        target_lang,
        "-od",
        str(work_dir),
        str(normalized_docx),
    ], work_dir, EXTRACT_TIMEOUT_SECONDS, "DOCUMENT_EXTRACTION_ERROR")
    if result.returncode != 0:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "Okapi extraction failed")

    xliff_path = work_dir / f"{normalized_docx.name}.xlf"
    _validate_xliff_file(xliff_path)
    blocks = _blocks_from_xliff(xliff_path)
    extraction_id = work_dir.name
    manifest = DocumentArtifactManifest(
        document_id=uuid.uuid4().hex,
        extraction_id=extraction_id,
        source_format=source_format,
        source_path=str(source),
        normalized_docx_path=str(normalized_docx),
        xliff_path=str(xliff_path),
        skeleton_path=str(normalized_docx),
        work_dir=str(work_dir),
        filter_config_id=CONFIG_ID,
        source_lang=source_lang,
        target_lang=target_lang,
        fidelity_level=fidelity_level,
        conversion_warnings=conversion_warnings,
        source_sha256=source_sha,
        normalized_docx_sha256=normalized_docx_sha,
        normalized_from_doc=normalized_from_doc,
        libreoffice_executable_path=libreoffice_executable_path,
        libreoffice_version=libreoffice_version,
        original_structure_snapshot=original_snapshot,
        filter_config_sha256=config_sha,
        conversion_metadata=conversion_metadata,
    )
    return PreprocessResult(
        protected_md=_build_markdown_view(normalized_docx),
        chunks=[],
        token_estimate_total=0,
        chunk_count=0,
        blocks=blocks,
        conversion_warnings=conversion_warnings,
        schema_version="2.1",
        source_document_path=str(source),
        normalized_docx_path=str(normalized_docx),
        xliff_path=str(xliff_path),
        fidelity_level=fidelity_level,
        source_lang=source_lang,
        target_lang=target_lang,
        work_dir=str(work_dir),
        document_manifest=manifest,
        original_structure_snapshot=original_snapshot,
        okapi_filter_config_id=CONFIG_ID,
        okapi_filter_config_sha256=config_sha,
        extraction_id=extraction_id,
    )


def merge_translations(
    document: PreprocessResult,
    translated_blocks: list[DocumentBlock],
    session_id: str | None = None,
) -> str:
    _validate_native_document(document)
    _validate_session_id(session_id)
    _ensure_runtime()
    source_by_id = {block.block_id: block for block in document.blocks}
    translated_by_id = _validate_translated_blocks(translated_blocks, source_by_id)

    work_dir = Path(document.work_dir)
    merge_dir = work_dir / f"merge-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    merge_dir.mkdir(parents=True)
    target_xliff = merge_dir / Path(document.xliff_path).name
    shutil.copy2(document.xliff_path, target_xliff)
    _write_targets(target_xliff, source_by_id, translated_by_id, document.target_lang)
    _verify_written_targets(target_xliff, source_by_id, translated_by_id)

    result = _run_tikal([
        str(TIKAL),
        "-m",
        "-fc",
        CONFIG_ID,
        "-sl",
        document.source_lang,
        "-tl",
        document.target_lang,
        "-sd",
        str(work_dir),
        "-od",
        str(merge_dir),
        str(target_xliff),
    ], work_dir, MERGE_TIMEOUT_SECONDS, "DOCUMENT_MERGE_ERROR")
    if result.returncode != 0:
        raise doc_error("DOCUMENT_MERGE_ERROR", "Okapi merge failed")

    output_path = merge_dir / Path(document.normalized_docx_path).name
    if Path(document.normalized_docx_path).resolve() == output_path.resolve():
        raise doc_error("DOCUMENT_MERGE_ERROR", "merge would overwrite source DOCX")
    cjk_font_result = apply_cjk_fonts(output_path)
    baseline_png_dir = None
    if document.fidelity_level == "approximate":
        # Reuse the normalized DOCX page renders from the pdf-readability audit as the
        # image-visibility baseline for the final render. The baseline is qualified ONLY
        # when Layer 1 (PDF normalization image visibility) passed; otherwise the
        # pdf-readability render may itself contain blank figures and is not trustworthy.
        readability_dir = Path(document.work_dir) / "pdf-readability"
        readability = (document.document_manifest.conversion_metadata or {}).get("readability") or {}
        image_visibility = readability.get("image_visibility") or {}
        layer1_ok = bool(image_visibility.get("image_visibility_checked")) and int(
            image_visibility.get("invisible_image_count", 1)
        ) == 0
        if layer1_ok and readability_dir.is_dir():
            if list(readability_dir.glob("render_page-*.png")) and list(readability_dir.glob("*.pdf")):
                baseline_png_dir = readability_dir
    quality = validate_delivery_docx(
        output_path,
        document.original_structure_snapshot,
        merge_dir,
        document.fidelity_level,
        baseline_png_dir=baseline_png_dir,
    )
    if document.document_manifest is not None:
        document.document_manifest.delivery_quality = quality.to_public_dict()
        document.document_manifest.delivery_quality["cjk_font"] = cjk_font_result
    return str(output_path)


def _blocks_from_xliff(xliff_path: Path) -> list[DocumentBlock]:
    root = ET.parse(xliff_path).getroot()
    namespace = namespace_of(root.tag)
    if namespace != XLIFF_NS:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "unsupported XLIFF namespace")
    blocks: list[DocumentBlock] = []
    seen: set[str] = set()
    for file_node in root.findall(qname(namespace, "file")):
        original = file_node.get("original", "")
        for unit in file_node.findall(f".//{qname(namespace, 'trans-unit')}"):
            unit_id = unit.get("id") or ""
            if not unit_id:
                raise doc_error("DOCUMENT_EXTRACTION_ERROR", "XLIFF trans-unit missing id")
            if unit_id in seen:
                raise doc_error("DOCUMENT_EXTRACTION_ERROR", "XLIFF trans-unit id is duplicated")
            seen.add(unit_id)
            source = unit.find(qname(namespace, "source"))
            if source is None:
                raise doc_error("DOCUMENT_EXTRACTION_ERROR", f"XLIFF trans-unit {unit_id} missing source")
            text, codec_metadata = encode_source(source)
            metadata = {
                **codec_metadata,
                "xliff_file_original": original,
                "xliff_unit_id": unit_id,
                "source_inline_signature": inline_signature(source),
            }
            blocks.append(DocumentBlock(
                block_id=unit_id,
                block_type="text",
                source_text=text,
                text=text,
                order=len(blocks),
                metadata=metadata,
            ))
    if not blocks:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "XLIFF contains zero trans-unit elements")
    return blocks


def _write_targets(
    xliff_path: Path,
    source_by_id: dict[str, DocumentBlock],
    translated_by_id: dict[str, DocumentBlock],
    target_lang: str,
) -> None:
    tree = ET.parse(xliff_path)
    root = tree.getroot()
    namespace = namespace_of(root.tag)
    for unit in root.findall(f".//{qname(namespace, 'trans-unit')}"):
        unit_id = unit.get("id") or ""
        source_block = source_by_id.get(unit_id)
        translated_block = translated_by_id.get(unit_id)
        if source_block is None or translated_block is None:
            raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "XLIFF IDs do not match extracted document")
        translated_text = _block_text(translated_block)
        assert_placeholder_contract(translated_text, source_block.metadata)
        source = unit.find(qname(namespace, "source"))
        if source is None:
            raise doc_error("DOCUMENT_MERGE_ERROR", f"XLIFF trans-unit {unit_id} missing source")
        target = restore_target(source, translated_text, source_block.metadata, target_lang)
        old_target = unit.find(qname(namespace, "target"))
        if old_target is None:
            source_index = list(unit).index(source)
            unit.insert(source_index + 1, target)
        else:
            index = list(unit).index(old_target)
            unit.remove(old_target)
            unit.insert(index, target)
    tree.write(xliff_path, encoding="utf-8", xml_declaration=True)


def _verify_written_targets(
    xliff_path: Path,
    source_by_id: dict[str, DocumentBlock],
    translated_by_id: dict[str, DocumentBlock],
) -> None:
    root = ET.parse(xliff_path).getroot()
    namespace = namespace_of(root.tag)
    seen: set[str] = set()
    for unit in root.findall(f".//{qname(namespace, 'trans-unit')}"):
        unit_id = unit.get("id") or ""
        if unit_id in seen:
            raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "duplicate trans-unit id after target write")
        seen.add(unit_id)
        target_nodes = unit.findall(qname(namespace, "target"))
        if len(target_nodes) != 1:
            raise doc_error("DOCUMENT_MERGE_ERROR", f"trans-unit {unit_id} must have exactly one target")
        source_block = source_by_id.get(unit_id)
        if source_block is None:
            raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "unknown trans-unit id after target write")
        translated_text = _block_text(translated_by_id[unit_id])
        assert_placeholder_contract(translated_text, source_block.metadata)
        if inline_signature(target_nodes[0]) != source_block.metadata.get("source_inline_signature", []):
            raise doc_error("DOCUMENT_PLACEHOLDER_ERROR", f"inline XML signature changed for {unit_id}")
        encoded_text, _ = encode_source(target_nodes[0])
        if encoded_text != translated_text:
            raise doc_error("DOCUMENT_PLACEHOLDER_ERROR", f"target text encoding changed for {unit_id}")
    if seen != set(source_by_id):
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "target XLIFF ID set changed")


def _validate_translated_blocks(
    translated_blocks: list[DocumentBlock],
    source_by_id: dict[str, DocumentBlock],
) -> dict[str, DocumentBlock]:
    translated_by_id: dict[str, DocumentBlock] = {}
    duplicates: set[str] = set()
    for block in translated_blocks:
        if block.block_id in translated_by_id:
            duplicates.add(block.block_id)
        translated_by_id[block.block_id] = block
    if duplicates:
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "duplicate translated block id")
    source_ids = set(source_by_id)
    translated_ids = set(translated_by_id)
    if source_ids != translated_ids:
        if source_ids - translated_ids:
            raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "missing translated block id")
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "unknown translated block id")
    for block_id, block in translated_by_id.items():
        assert_placeholder_contract(_block_text(block), source_by_id[block_id].metadata)
    return translated_by_id


def _validate_native_document(document: PreprocessResult) -> None:
    if getattr(document, "fidelity_level", "") not in {"native", "normalized", "approximate"}:
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "merge requires an extract_document() result")
    required = [document.normalized_docx_path, document.xliff_path, document.work_dir, document.source_lang, document.target_lang]
    if not all(required) or not document.blocks:
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "native document result is incomplete")
    if document.fidelity_level == "approximate":
        manifest = getattr(document, "document_manifest", None)
        if (
            manifest is None
            or manifest.source_format != FormatType.PDF.value
            or manifest.fidelity_level != "approximate"
            or manifest.normalized_docx_path != document.normalized_docx_path
            or not manifest.normalized_docx_sha256
            or not manifest.conversion_metadata
        ):
            raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "PDF document result is incomplete")
    if document.okapi_filter_config_id != CONFIG_ID:
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "native document was extracted with an unexpected Okapi config")
    if _sha256_file(CONFIG_SOURCE) != document.okapi_filter_config_sha256:
        raise doc_error("DOCUMENT_RUNTIME_UNAVAILABLE", "Okapi filter config checksum changed")


def _validate_output_docx(output_path: Path, expected_snapshot: dict) -> None:
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise doc_error("DOCUMENT_MERGE_ERROR", "merged DOCX was not created")
    if output_path.stat().st_size > MAX_OUTPUT_DOCX_BYTES:
        raise doc_error("DOCUMENT_INTEGRITY_ERROR", "merged DOCX exceeds size limit")
    try:
        with zipfile.ZipFile(output_path) as zf:
            bad = zf.testzip()
            if bad:
                raise doc_error("DOCUMENT_INTEGRITY_ERROR", f"DOCX ZIP CRC failed for member {bad}")
            for name in zf.namelist():
                if name.endswith(".xml") or name.endswith(".rels"):
                    raw = zf.read(name)
                    ET.fromstring(raw)
                    if b"[[TA_" in raw:
                        raise doc_error("DOCUMENT_PLACEHOLDER_ERROR", "internal placeholder remained in merged DOCX")
    except zipfile.BadZipFile as exc:
        raise doc_error("DOCUMENT_INTEGRITY_ERROR", "merged output is not a valid DOCX ZIP") from exc
    after = snapshot_docx_structure(str(output_path))
    if after != expected_snapshot:
        raise doc_error("DOCUMENT_INTEGRITY_ERROR", "merged DOCX structure differs from source skeleton")


def _validate_xliff_file(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "Okapi did not create a non-empty XLIFF")
    if path.stat().st_size > MAX_XLIFF_BYTES:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "XLIFF exceeds size limit")
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        raise doc_error("DOCUMENT_EXTRACTION_ERROR", "XLIFF is not parseable XML") from exc


def _validate_session_id(session_id: str | None) -> str:
    if session_id is None:
        return uuid.uuid4().hex
    if not SESSION_ID_RE.match(session_id) or ".." in session_id:
        raise doc_error("DOCUMENT_TRANSLATION_CONTRACT_ERROR", "invalid session id")
    return session_id


def _create_work_dir(session_id: str | None) -> Path:
    safe_session = _validate_session_id(session_id)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    extraction_id = f"{safe_session}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    work_dir = RUNS_DIR / extraction_id
    work_dir.mkdir()
    return work_dir


def _safe_docx_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    if not safe.lower().endswith(".docx"):
        safe += ".docx"
    return safe or "source.docx"


def _ensure_runtime() -> None:
    if not TIKAL.exists() or not os.access(TIKAL, os.X_OK):
        raise doc_error("DOCUMENT_RUNTIME_UNAVAILABLE", "Okapi Tikal runtime is unavailable")
    if not CONFIG_SOURCE.exists() or CONFIG_SOURCE.stat().st_size == 0:
        raise doc_error("DOCUMENT_RUNTIME_UNAVAILABLE", "Okapi P0 filter config is unavailable")


def _run_tikal(cmd: list[str], cwd: Path, timeout: int, error_code: str) -> CommandResult:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise doc_error(error_code, "Okapi command timed out") from exc
    except OSError as exc:
        raise doc_error("DOCUMENT_RUNTIME_UNAVAILABLE", "Okapi runtime could not be started") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


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


def _heading_level(style_name: str) -> int:
    match = re.search(r"(\d+)", style_name)
    return int(match.group(1)) if match else 1


def _build_markdown_view(normalized_docx: Path) -> str:
    """Build a markdown format-context view of the normalized DOCX.

    This is a format-context view only (headings/tables/images), NOT a translation
    contract and NOT a merge carrier. The translation contract remains `blocks`;
    merge still goes through Okapi (XLIFF + skeleton).
    """
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(normalized_docx))
    image_targets: dict[str, str] = {}
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            image_targets[rel.rId] = rel.target_ref or "image.png"

    lines: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            paragraph = Paragraph(child, doc)
            for blip in child.findall(".//" + qn("a:blip")):
                rid = blip.get(qn("r:embed")) or ""
                lines.append(f"![image]({image_targets.get(rid, 'image.png')})")
            text = paragraph.text.strip()
            style_name = paragraph.style.name if paragraph.style is not None else ""
            if style_name.lower().startswith("heading"):
                lines.append(f"{'#' * _heading_level(style_name)} {text}".rstrip())
            elif text:
                lines.append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue
            header = rows[0]
            table_lines = ["| " + " | ".join(header) + " |"]
            table_lines.append("| " + " | ".join("---" for _ in header) + " |")
            for row in rows[1:]:
                table_lines.append("| " + " | ".join(row) + " |")
            lines.append("\n".join(table_lines))

    return "\n\n".join(lines)
