"""PDF-to-DOCX readability audit for approximate normalization."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline.document_quality import (
    DocumentQualityError,
    extract_pdf_text,
    pdf_page_count,
    quality_error,
    render_docx_to_pdf,
    render_pdf_to_pngs,
    is_png_nonblank,
)
from transagent.backend.pipeline import pdf_overlap
from transagent.backend.pipeline.pdf_image_visibility import check_pdf_image_visibility
from transagent.backend.pipeline.pdf_normalizer import resolve_pdf_runtime


CONTENT_WARNING_THRESHOLD = 0.90
CONTENT_BLOCK_THRESHOLD = 0.60
ORDER_WARNING_THRESHOLD = 0.20
PAGE_CHANGE_WARNING_THRESHOLD = 0.50

READING_ORDER_WARNING = "PDF column reading order may require manual review."
OBJECT_LOSS_WARNING = "PDF non-text layout objects may not have been fully preserved."


@dataclass
class PdfReadabilityResult:
    metadata: dict
    warnings: list[str] = field(default_factory=list)


def audit_pdf_readability(source_pdf: Path, normalized_docx: Path, work_dir: Path) -> PdfReadabilityResult:
    """Audit approximate PDF normalization without storing source or rendered full text."""
    audit_dir = work_dir / "pdf-readability"
    audit_dir.mkdir(parents=True, exist_ok=True)
    source_info = _inspect_source_pdf(source_pdf, include_text=True, include_layout=True)
    source_pages = int(source_info.get("page_count") or 0)
    source_text = _normalize_text("\n".join(source_info.get("pages_text") or []))

    rendered_pdf = render_docx_to_pdf(normalized_docx, audit_dir)
    rendered_pages = pdf_page_count(rendered_pdf)
    pngs = render_pdf_to_pngs(rendered_pdf, audit_dir, rendered_pages)
    blank_pages = [index + 1 for index, path in enumerate(pngs) if not is_png_nonblank(path)]
    try:
        image_visibility = check_pdf_image_visibility(source_pdf, rendered_pdf)
    except Exception as exc:
        code = "DOCUMENT_INTEGRITY_ERROR"
        if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE"):
            code = "DOCUMENT_RUNTIME_UNAVAILABLE"
        raise quality_error(code, "PDF normalization image visibility check failed") from exc
    if image_visibility["invisible_image_count"] > 0:
        raise quality_error(
            "DOCUMENT_INTEGRITY_ERROR",
            "PDF normalization lost visible source figure(s) "
            f"({image_visibility['invisible_image_count']} of {image_visibility['meaningful_image_count']} "
            f"figures invisible; matched {image_visibility['matched_visible_image_count']})",
        )
    rendered_text = _normalize_text(extract_pdf_text(rendered_pdf, audit_dir))
    overlap_metrics = pdf_overlap.detect_rendered_text_image_overlap(rendered_pdf)

    anchors = _build_anchors(source_text)
    anchor_coverage = _anchor_coverage(anchors, rendered_text)
    sequence_similarity = SequenceMatcher(None, source_text, rendered_text).ratio() if source_text and rendered_text else 0.0
    fuzzy_token_coverage = _token_coverage(source_text, rendered_text)
    length_coverage = _length_coverage(source_text, rendered_text)
    evidence_coverage = max(anchor_coverage, sequence_similarity, fuzzy_token_coverage)
    content_retention_ratio = round(min(length_coverage, evidence_coverage), 4)
    order_metrics = _reading_order_metrics(anchors, rendered_text)
    order_ratio = round(order_metrics["reading_order_inversion_ratio"], 4)

    source_image_count = int(source_info.get("image_count") or 0)
    source_table_count = source_info.get("table_count")
    docx_snapshot = snapshot_docx_structure(str(normalized_docx))
    docx_image_count = int(docx_snapshot.get("image_count") or 0)
    docx_table_count = int(docx_snapshot.get("table_count") or 0)
    page_change_ratio = round(abs(rendered_pages - source_pages) / source_pages, 4) if source_pages else 0.0

    warnings: list[str] = []
    if source_pages and page_change_ratio > PAGE_CHANGE_WARNING_THRESHOLD:
        warnings.append(f"PDF rendered page count changed from {source_pages} to {rendered_pages}.")
    if source_pages and rendered_pages / source_pages >= 3:
        warnings.append("One source PDF page expanded to three or more DOCX pages.")
    if blank_pages:
        warnings.append("PDF normalization rendered blank DOCX pages.")
    if content_retention_ratio < CONTENT_WARNING_THRESHOLD:
        warnings.append(f"PDF content retention ratio {content_retention_ratio:.2f} is below 0.90 warning threshold.")
    if order_ratio > ORDER_WARNING_THRESHOLD or bool(source_info.get("column_layout_risk")):
        warnings.append(READING_ORDER_WARNING)
    if source_image_count > 0 and docx_image_count == 0:
        warnings.append(OBJECT_LOSS_WARNING)
    if source_table_count is None:
        if docx_table_count == 0 and source_image_count:
            warnings.append("PDF table preservation is uncertain; manual review required.")
    elif int(source_table_count) > 0 and docx_table_count == 0:
        warnings.append(OBJECT_LOSS_WARNING)

    metadata = {
        "source_page_count": source_pages,
        "normalized_rendered_page_count": rendered_pages,
        "page_count_change_ratio": page_change_ratio,
        "blank_pages": blank_pages,
        "content_retention_ratio": content_retention_ratio,
        "source_normalized_char_count": len(source_text),
        "rendered_normalized_char_count": len(rendered_text),
        "length_coverage_ratio": round(length_coverage, 4),
        "anchor_coverage_ratio": round(anchor_coverage, 4),
        "sequence_similarity_ratio": round(sequence_similarity, 4),
        "fuzzy_token_coverage_ratio": round(fuzzy_token_coverage, 4),
        "content_retention_warning_threshold": CONTENT_WARNING_THRESHOLD,
        "content_retention_block_threshold": CONTENT_BLOCK_THRESHOLD,
        "content_retention_basis": "min(normalized length coverage, max(anchor coverage, normalized character sequence similarity, fuzzy token coverage))",
        "content_retention_algorithm": "Fuzzy token coverage is noise-tolerant evidence only; final retention is bounded by normalized character length coverage.",
        "anchor_count": len(anchors),
        "found_anchor_count": order_metrics["found_anchor_count"],
        "comparable_anchor_pairs": order_metrics["comparable_anchor_pairs"],
        "token_coverage_ratio": round(fuzzy_token_coverage, 4),
        "reading_order_inversion_ratio": order_ratio,
        "reading_order_warning_threshold": ORDER_WARNING_THRESHOLD,
        "source_image_count": source_image_count,
        "normalized_docx_image_count": docx_image_count,
        "source_table_count": source_table_count,
        "normalized_docx_table_count": docx_table_count,
        "text_image_overlap_count": overlap_metrics["text_image_overlap_count"],
        "text_image_overlap_max_ratio": overlap_metrics["text_image_overlap_max_ratio"],
        "text_image_overlap_block_threshold": 0,
        "image_visibility": image_visibility,
    }
    if overlap_metrics["text_image_overlap_count"] > 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF-normalized DOCX has severe text/image overlap")
    if content_retention_ratio < CONTENT_BLOCK_THRESHOLD:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF content retention ratio is below 0.60 block threshold")
    return PdfReadabilityResult(metadata=metadata, warnings=_dedupe(warnings))


def _inspect_source_pdf(source_pdf: Path, include_text: bool, include_layout: bool) -> dict:
    try:
        runtime = resolve_pdf_runtime()
    except Exception as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime is unavailable") from exc
    script = r"""
import json, sys
import fitz
doc = fitz.open(sys.argv[1])
pages_text = []
image_xrefs = set()
table_count = 0
table_known = True
column_layout_risk = False
for page in doc:
    text = page.get_text("text") or ""
    pages_text.append(text)
    for image in page.get_images(full=True):
        image_xrefs.add(image[0])
    if hasattr(page, "find_tables"):
        try:
            table_count += len(page.find_tables().tables)
        except Exception:
            table_known = False
    else:
        table_known = False
    blocks = [b for b in page.get_text("blocks") if len(b) >= 5 and str(b[4]).strip()]
    if len(blocks) >= 4:
        xs = sorted(float(b[0]) for b in blocks)
        span = max(xs) - min(xs) if xs else 0
        left = [x for x in xs if x < min(xs) + span * 0.45]
        right = [x for x in xs if x > min(xs) + span * 0.55]
        if len(left) >= 2 and len(right) >= 2:
            column_layout_risk = True
print(json.dumps({
    "page_count": len(doc),
    "pages_text": pages_text,
    "image_count": len(image_xrefs),
    "table_count": table_count if table_known else None,
    "column_layout_risk": column_layout_risk,
}, ensure_ascii=False))
"""
    try:
        result = subprocess.run([str(runtime), "-c", script, str(source_pdf)], text=True, capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF readability inspection runtime is unavailable") from exc
    if result.returncode != 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF readability inspection failed")
    try:
        return json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF readability inspection failed") from exc


def _normalize_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return re.sub(r"\s+", " ", text).strip()


def _build_anchors(text: str) -> list[str]:
    pieces = [piece.strip() for piece in re.split(r"[\n。.!?;；]", text) if len(piece.strip()) >= 8]
    if len(pieces) < 5 and text:
        pieces.extend(text[index:index + 48].strip() for index in range(0, len(text), 48))
    unique: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        anchor = piece[:96]
        if len(anchor) >= 8 and anchor not in seen:
            unique.append(anchor)
            seen.add(anchor)
        if len(unique) >= 200:
            break
    return unique


def _anchor_coverage(anchors: list[str], rendered_text: str) -> float:
    if not anchors:
        return 1.0 if rendered_text else 0.0
    found = sum(1 for anchor in anchors if anchor in rendered_text)
    return found / len(anchors)


def _token_coverage(source_text: str, rendered_text: str) -> float:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_:/?.=&+-]{2,}", source_text)
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            unique.append(token)
            seen.add(token)
    if not unique:
        return 1.0 if rendered_text else 0.0
    rendered_tokens = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9_:/?.=&+-]{2,}", rendered_text))
    found = 0
    for token in unique:
        if token in rendered_text:
            found += 1
            continue
        if any(abs(len(token) - len(candidate)) <= 2 and SequenceMatcher(None, token, candidate).ratio() >= 0.82 for candidate in rendered_tokens):
            found += 1
    return found / len(unique)


def _length_coverage(source_text: str, rendered_text: str) -> float:
    if not source_text:
        return 0.0
    return min(1.0, len(rendered_text) / len(source_text))


def _reading_order_metrics(anchors: list[str], rendered_text: str) -> dict:
    positions = [rendered_text.find(anchor) for anchor in anchors]
    positions = [pos for pos in positions if pos >= 0]
    if len(positions) < 2:
        return {
            "found_anchor_count": len(positions),
            "comparable_anchor_pairs": 0,
            "reading_order_inversion_ratio": 0.0,
        }
    inversions = 0
    total_pairs = 0
    for left_index, left in enumerate(positions):
        for right in positions[left_index + 1:]:
            total_pairs += 1
            if right < left:
                inversions += 1
    return {
        "found_anchor_count": len(positions),
        "comparable_anchor_pairs": total_pairs,
        "reading_order_inversion_ratio": inversions / total_pairs if total_pairs else 0.0,
    }


def _reading_order_inversion_ratio(anchors: list[str], rendered_text: str) -> float:
    return _reading_order_metrics(anchors, rendered_text)["reading_order_inversion_ratio"]


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result
