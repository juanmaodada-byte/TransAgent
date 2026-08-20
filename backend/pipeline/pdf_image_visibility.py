"""Two-layer per-image visibility gate between PDFs.

Layer 1 (PDF normalization safety): reference = source PDF, candidate = the normalized
DOCX render. Layer 2 (final merge regression): reference = the normalized render (only
usable as a baseline once Layer 1 passed), candidate = the final delivery render.

Visibility is decided by two independent content-level evidence kinds, never geometry:

1. Content identity — the decoded-pixel digest (PyMuPDF, byte-exact) and a 16x16 absolute
   grayscale thumbnail (area-average resample, values 0..1, NOT centered) compared with L1
   distance. Digest matches exactly when pixels are byte-identical; the thumbnail covers
   re-encode / color-space shifts and scale changes while still distinguishing pure-white
   from pure-color from line-art.
2. Actual rendered visibility — the candidate's placed bbox is rendered and its non-white
   pixel ratio must exceed a small floor. Pure-white / near-white / transparent => invisible.
   Grayscale, line-art, and white-background figures pass because the check is white/non-white,
   never "must have color".

Geometry (aspect ratio, placed area) is diagnostic only: it is recorded for the public
metric ``minimum_visible_area_ratio`` and used to prune the meaningful set, but it can never
turn a content mismatch into ``matched=True``. Page numbers are never part of identity
matching (PDF->DOCX reflow shifts pages unpredictably).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from transagent.backend.pipeline.pdf_normalizer import PDF_IMAGE_VISIBILITY_WORKER, resolve_pdf_runtime


ROOT = Path(__file__).resolve().parents[2]
# Single source of truth lives in pdf_normalizer.PDF_IMAGE_VISIBILITY_WORKER (and
# PRODUCTION_PDF_WORKERS); keep this alias for backward-compatible references.
IMAGE_VISIBILITY_WORKER = PDF_IMAGE_VISIBILITY_WORKER

# Minimum placed area (square points) for an image to count as a meaningful figure; smaller
# objects (icons, bullets, decorations) are excluded from the gate.
IMAGE_MIN_VISIBLE_AREA_PTS = 400.0
# L1 distance ceiling on the 16x16 absolute grayscale thumbnail for two images to be the
# same content. Real identical pairs (source vs normalized render) measured 0.0004..0.0010;
# the nearest different-content pair across the Cloud sample measured 0.0493. 0.02 leaves a
# wide margin on both sides.
IMAGE_IDENTITY_L1_THRESHOLD = 0.02
# Fraction of the candidate's placed-bbox render that must be non-white for the figure to
# count as actually visible. Pure-white / transparent renders 0.0; line-art and grayscale
# figures render well above this floor.
IMAGE_VISIBLE_NONWHITE_MIN_RATIO = 0.001
VISIBILITY_TIMEOUT_SECONDS = 60

CONTENT_MATCH_METHOD = "decoded_pixel_digest+grayscale_thumbnail_l1"


class PdfImageVisibilityError(ValueError):
    """Stable DOCUMENT_* prefixed image visibility error."""


def pdf_image_visibility_error(code: str, detail: str) -> PdfImageVisibilityError:
    return PdfImageVisibilityError(f"{code}: {detail}")


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _aspect(bbox: list[float]) -> float:
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    if height <= 0:
        return 0.0
    return width / height


def _thumb_l1(a: list[float], b: list[float]) -> float:
    """Mean absolute distance between two equal-length thumbnails (values in 0..1)."""
    if not a or not b or len(a) != len(b):
        return float("inf")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _content_distance(ref: dict, cand: dict) -> float:
    """Content identity distance: 0.0 for byte-exact digest, else thumbnail L1."""
    ref_digest = ref.get("digest") or ""
    cand_digest = cand.get("digest") or ""
    if ref_digest and cand_digest and ref_digest == cand_digest:
        return 0.0
    return _thumb_l1(ref.get("thumb") or [], cand.get("thumb") or [])


def collect_pdf_images(pdf_path: Path) -> dict:
    """Run the PyMuPDF worker; returns openable/page_count/images payload."""
    runtime = resolve_pdf_runtime()
    if not IMAGE_VISIBILITY_WORKER.exists() or IMAGE_VISIBILITY_WORKER.stat().st_size == 0:
        raise pdf_image_visibility_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF image visibility worker is unavailable")
    try:
        result = subprocess.run(
            [str(runtime), str(IMAGE_VISIBILITY_WORKER), "--input", str(pdf_path)],
            text=True,
            capture_output=True,
            timeout=VISIBILITY_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise pdf_image_visibility_error("DOCUMENT_INTEGRITY_ERROR", "PDF image visibility inspection timed out") from exc
    except OSError as exc:
        raise pdf_image_visibility_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF image visibility runtime could not be started") from exc
    if result.returncode != 0:
        raise pdf_image_visibility_error("DOCUMENT_INTEGRITY_ERROR", "PDF image visibility inspection failed")
    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise pdf_image_visibility_error("DOCUMENT_INTEGRITY_ERROR", "PDF image visibility worker returned invalid JSON") from exc
    if not payload.get("openable"):
        raise pdf_image_visibility_error("DOCUMENT_INTEGRITY_ERROR", "PDF image visibility inspection failed")
    return payload


def meaningful_images(images: list[dict], *, min_area: float = IMAGE_MIN_VISIBLE_AREA_PTS) -> list[dict]:
    return [image for image in images if _bbox_area(image["bbox"]) >= min_area]


def _is_candidate_visible(cand: dict) -> bool:
    ratio = cand.get("render_nonwhite_ratio")
    if ratio is None:
        return False
    try:
        return float(ratio) >= IMAGE_VISIBLE_NONWHITE_MIN_RATIO
    except (TypeError, ValueError):
        return False


def match_reference_images(
    reference_images: list[dict],
    candidate_images: list[dict],
    *,
    min_area: float = IMAGE_MIN_VISIBLE_AREA_PTS,
    identity_l1_threshold: float = IMAGE_IDENTITY_L1_THRESHOLD,
) -> list[dict]:
    """Match every meaningful reference image against a meaningful candidate by content.

    Greedy one-to-one assignment over content identity (digest first, then thumbnail L1).
    A match requires BOTH a content identity match AND the candidate to be actually visible
    (non-white rendered bbox). Returns one entry per meaningful reference image with
    ``matched`` (identity + visibility), ``content_matched``, ``candidate_visible``,
    ``area_ratio`` and ``aspect_ratio`` diagnostics.
    """
    refs = meaningful_images(reference_images, min_area=min_area)
    cands = meaningful_images(candidate_images, min_area=min_area)
    used: set[int] = set()
    matches: list[dict] = []
    for ref in refs:
        ref_area = _bbox_area(ref["bbox"])
        ref_aspect = _aspect(ref["bbox"])
        best_index = None
        best_distance = float("inf")
        for index, cand in enumerate(cands):
            if index in used:
                continue
            distance = _content_distance(ref, cand)
            if distance <= identity_l1_threshold and distance < best_distance:
                best_distance = distance
                best_index = index
        if best_index is None:
            matches.append(
                {
                    "page": ref["page"],
                    "matched": False,
                    "content_matched": False,
                    "candidate_visible": False,
                    "area_ratio": 0.0,
                    "aspect_ratio": 0.0,
                }
            )
            continue
        used.add(best_index)
        cand = cands[best_index]
        cand_area = _bbox_area(cand["bbox"])
        cand_aspect = _aspect(cand["bbox"])
        area_ratio = cand_area / ref_area if ref_area > 0 else 0.0
        aspect_ratio = cand_aspect / ref_aspect if ref_aspect > 0 else 0.0
        candidate_visible = _is_candidate_visible(cand)
        matches.append(
            {
                "page": ref["page"],
                "matched": candidate_visible,
                "content_matched": True,
                "candidate_visible": candidate_visible,
                "area_ratio": round(area_ratio, 4),
                "aspect_ratio": round(aspect_ratio, 4),
            }
        )
    return matches


def build_visibility_metrics(matches: list[dict]) -> dict:
    """Public, non-sensitive metrics shared by both layers.

    ``minimum_visible_area_ratio`` is the worst symmetric area ratio over matched figures
    (1.0 = exact size, lower = resized during reflow). ``blank_candidate_count`` counts
    content-matched candidates whose rendered bbox is blank (invisible).
    """
    meaningful = len(matches)
    matched_visible = sum(1 for item in matches if item["matched"])
    blank_candidates = sum(1 for item in matches if item.get("content_matched") and not item.get("candidate_visible"))
    ratios = []
    for item in matches:
        if item["matched"] and item["area_ratio"] > 0:
            ratios.append(min(item["area_ratio"], 1.0 / item["area_ratio"]))
    min_ratio = min(ratios, default=1.0)
    return {
        "image_visibility_checked": True,
        "meaningful_image_count": meaningful,
        "matched_visible_image_count": matched_visible,
        "minimum_visible_area_ratio": round(min_ratio, 4),
        "invisible_image_count": meaningful - matched_visible,
        "content_match_method": CONTENT_MATCH_METHOD,
        "blank_candidate_count": blank_candidates,
    }


def check_pdf_image_visibility(reference_pdf: Path, candidate_pdf: Path) -> dict:
    """Shared gate: every meaningful reference image must match a visible candidate image.

    Layer 1 callers pass (source_pdf, normalized_render_pdf); Layer 2 callers pass
    (qualified_baseline_render_pdf, final_render_pdf).
    """
    reference = collect_pdf_images(reference_pdf)
    candidate = collect_pdf_images(candidate_pdf)
    matches = match_reference_images(reference["images"], candidate["images"])
    return build_visibility_metrics(matches)
