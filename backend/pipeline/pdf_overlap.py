"""Rendered PDF text/image overlap detection shared by PDF audits and delivery gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from transagent.backend.pipeline.document_quality import quality_error
from transagent.backend.pipeline.pdf_normalizer import resolve_pdf_runtime


def detect_rendered_text_image_overlap(rendered_pdf: Path) -> dict:
    """Return public, text-free overlap metrics for a rendered PDF."""
    try:
        runtime = resolve_pdf_runtime()
    except Exception as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime is unavailable") from exc
    script = r"""
import json, sys
import fitz
doc = fitz.open(sys.argv[1])
overlaps = []
for page_index, page in enumerate(doc, start=1):
    blocks = page.get_text("dict").get("blocks", [])
    text_blocks = []
    image_blocks = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox:
            continue
        rect = fitz.Rect(bbox)
        if block.get("type") == 0:
            text = " ".join(span.get("text", "") for line in block.get("lines", []) for span in line.get("spans", []))
            if len(text.strip()) >= 20 and rect.get_area() > 20:
                text_blocks.append(rect)
        elif block.get("type") == 1 and rect.get_area() > 200:
            image_blocks.append(rect)
    for text_rect in text_blocks:
        for image_rect in image_blocks:
            inter = text_rect & image_rect
            if inter.is_empty:
                continue
            overlap_area = inter.get_area()
            ratio = overlap_area / max(text_rect.get_area(), 1)
            if overlap_area >= 12 and ratio >= 0.10:
                overlaps.append({"page": page_index, "ratio": ratio, "area": overlap_area})
print(json.dumps({
    "text_image_overlap_count": len(overlaps),
    "text_image_overlap_max_ratio": max([item["ratio"] for item in overlaps], default=0.0),
}, ensure_ascii=False))
"""
    try:
        result = subprocess.run([str(runtime), "-c", script, str(rendered_pdf)], text=True, capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF readability inspection runtime is unavailable") from exc
    if result.returncode != 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF readability inspection failed")
    try:
        data = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "PDF readability inspection failed") from exc
    return {
        "text_image_overlap_count": int(data.get("text_image_overlap_count") or 0),
        "text_image_overlap_max_ratio": round(float(data.get("text_image_overlap_max_ratio") or 0.0), 4),
    }
