"""Tests for the two-layer per-image visibility gate (backend/pipeline/pdf_image_visibility.py).

The gate must NOT be geometry-only. It decides visibility from two content-level evidence
kinds (decoded-pixel digest + absolute grayscale thumbnail) plus an actual rendered-visibility
check on the candidate's placed bbox. These tests exercise that contract directly, including
the adversarial cases that a geometry-only matcher silently passes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from transagent.backend.pipeline import pdf_image_visibility as visibility
from transagent.backend.pipeline.pdf_image_visibility import (
    IMAGE_IDENTITY_L1_THRESHOLD,
    build_visibility_metrics,
    check_pdf_image_visibility,
    collect_pdf_images,
    match_reference_images,
    meaningful_images,
)


# ---------------------------------------------------------------------------
# Pure-function helpers: thumbnails for solid colors and a line-art pattern.
# ---------------------------------------------------------------------------

def _solid_thumb(value: float) -> list[float]:
    return [value] * 256


def _img(page: int, x0: float, y0: float, x1: float, y1: float, *,
         thumb: list[float] | None = None, digest: str = "", ratio: float = 0.5) -> dict:
    return {
        "page": page,
        "bbox": [x0, y0, x1, y1],
        "width": 0,
        "height": 0,
        "xref": 0,
        "digest": digest,
        "thumb": thumb if thumb is not None else [0.0] * 256,
        "render_nonwhite_ratio": ratio,
    }


RED = _solid_thumb(0.299)
WHITE = _solid_thumb(1.0)
GREEN = _solid_thumb(0.587)
GRAY128 = _solid_thumb(128 / 255)


def test_red_source_white_candidate_is_invisible():
    # Adversarial #1: a red figure replaced by a same-position/size pure-white figure must
    # NOT be reported visible. Geometry is identical, content is not.
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=WHITE)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is False
    metrics = build_visibility_metrics(matches)
    assert metrics["invisible_image_count"] == 1


def test_red_source_different_content_same_geometry_is_not_matched_by_geometry():
    # Adversarial #2: same size, non-white but different content must not pass on geometry.
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=GREEN)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is False
    assert matches[0]["content_matched"] is False


def test_same_image_page_change_matches():
    # #3: identical content, different page, must match (page is not an identity key).
    reference = [_img(1, 50, 50, 250, 210, thumb=RED, digest="aa")]
    candidate = [_img(7, 50, 50, 250, 210, thumb=RED, digest="aa")]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is True


def test_same_image_rescaled_matches():
    # #4: identical content, rescaled (different placed area), must match via content.
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 350, 270, thumb=RED)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is True


def test_same_grayscale_image_matches():
    # #5: identical grayscale content matches.
    reference = [_img(1, 50, 50, 250, 210, thumb=GRAY128, ratio=0.5)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=GRAY128, ratio=0.5)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is True


def test_line_art_image_matches():
    # #6: white background black-line technical figure passes visibility (non-white present).
    reference = [_img(1, 50, 50, 250, 210, thumb=WHITE, ratio=0.03)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=WHITE, ratio=0.03)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["matched"] is True


def test_two_same_references_one_candidate_leaves_one_invisible():
    # #7: two identical reference figures, only one candidate -> exactly one invisible.
    reference = [
        _img(1, 50, 50, 250, 210, thumb=RED),
        _img(2, 50, 50, 250, 210, thumb=RED),
    ]
    candidate = [_img(1, 50, 50, 250, 210, thumb=RED)]
    metrics = build_visibility_metrics(match_reference_images(reference, candidate))
    assert metrics["meaningful_image_count"] == 2
    assert metrics["matched_visible_image_count"] == 1
    assert metrics["invisible_image_count"] == 1


def test_content_matched_but_blank_render_is_invisible():
    # #8: the image object is still in the PDF (identity matches) but its rendered bbox is
    # blank (pure white). Must be invisible despite content identity matching.
    reference = [_img(1, 50, 50, 250, 210, thumb=WHITE, ratio=0.5)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=WHITE, ratio=0.0)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["content_matched"] is True
    assert matches[0]["matched"] is False
    metrics = build_visibility_metrics(matches)
    assert metrics["blank_candidate_count"] == 1
    assert metrics["invisible_image_count"] == 1


def test_red_kept_but_render_blanked_is_invisible():
    # Adversarial #9: the source red figure's image object survives with matching content
    # identity (same red thumbnail), but its placed bbox renders fully blank (ratio=0.0)
    # because it was cropped to white during reflow. Content identity matches, but the
    # figure is not actually visible -> must be invisible, not silently passed.
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=RED, ratio=0.0)]
    matches = match_reference_images(reference, candidate)
    assert matches[0]["content_matched"] is True
    assert matches[0]["matched"] is False
    metrics = build_visibility_metrics(matches)
    assert metrics["invisible_image_count"] == 1


def test_missing_candidate_is_invisible():
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    matches = match_reference_images(reference, [])
    assert matches[0]["matched"] is False
    metrics = build_visibility_metrics(matches)
    assert metrics["invisible_image_count"] == 1


def test_identity_threshold_rejects_near_but_different_content():
    # A thumbnail just above the L1 threshold must not match.
    near = [min(1.0, v + IMAGE_IDENTITY_L1_THRESHOLD + 0.01) for v in RED]
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=near)]
    assert match_reference_images(reference, candidate)[0]["matched"] is False


def test_tiny_objects_excluded_from_meaningful():
    tiny = _img(1, 50, 50, 70, 60, thumb=RED)
    assert meaningful_images([tiny]) == []


def test_metrics_include_content_method_and_no_sensitive_data():
    reference = [_img(1, 50, 50, 250, 210, thumb=RED)]
    candidate = [_img(1, 50, 50, 250, 210, thumb=RED)]
    metrics = build_visibility_metrics(match_reference_images(reference, candidate))
    assert metrics["image_visibility_checked"] is True
    assert metrics["content_match_method"] == visibility.CONTENT_MATCH_METHOD
    assert metrics["blank_candidate_count"] == 0
    # Public metrics are scalars / method names only: no digest VALUE, no thumbnail data,
    # no path, no command. (content_match_method is the algorithm name, not a value.)
    for key, value in metrics.items():
        if key == "content_match_method":
            continue
        assert isinstance(value, (int, float, bool)), f"{key} leaked non-scalar data"


# ---------------------------------------------------------------------------
# Runtime integration (PyMuPDF-generated PDFs) when the PDF runtime is available.
# ---------------------------------------------------------------------------

try:
    from transagent.backend.pipeline.pdf_normalizer import resolve_pdf_runtime

    PDF_RUNTIME = Path(resolve_pdf_runtime())
except Exception:  # pragma: no cover - environment dependent
    PDF_RUNTIME = None

FIXTURE_SCRIPT = visibility.ROOT / "scripts" / "pdf_make_fixture_pdf.py"


def _make_pdf(path: Path, *, rect=None, grayscale=False, color="red", pattern="solid") -> Path:
    cmd = [str(PDF_RUNTIME), str(FIXTURE_SCRIPT), "--output", str(path), "--color", color, "--pattern", pattern]
    if rect is not None:
        cmd += ["--rect"] + [str(value) for value in rect]
    if grayscale:
        cmd += ["--grayscale"]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    return path


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_red_to_white_is_blocked(tmp_path):
    # Adversarial end-to-end: source red figure, candidate same-geometry pure-white figure.
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="red")
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 250, 210), color="white")
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["invisible_image_count"] == 1
    assert metrics["matched_visible_image_count"] == 0


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_red_to_green_different_content_is_blocked(tmp_path):
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="red")
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 250, 210), color="green")
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["invisible_image_count"] == 1


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_same_red_image_rescaled_passes(tmp_path):
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="red")
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 350, 270), color="red")
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["invisible_image_count"] == 0
    assert metrics["matched_visible_image_count"] == 1


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_same_grayscale_image_passes(tmp_path):
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), grayscale=True)
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 250, 210), grayscale=True)
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["invisible_image_count"] == 0


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_line_art_figure_passes(tmp_path):
    # White-background black-line technical figure must pass (non-white pixels present).
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="black", pattern="lineart")
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 250, 210), color="black", pattern="lineart")
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["invisible_image_count"] == 0


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_layer1_blank_replacement_is_blocked(tmp_path):
    # Candidate keeps an image object but renders blank (pure white).
    source = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="white")
    candidate = _make_pdf(tmp_path / "candidate.pdf", rect=(50, 50, 250, 210), color="white")
    metrics = check_pdf_image_visibility(source, candidate)
    assert metrics["blank_candidate_count"] == 1
    assert metrics["invisible_image_count"] == 1


@pytest.mark.skipif(PDF_RUNTIME is None, reason="PDF runtime unavailable")
def test_worker_exposes_identity_and_visibility_fields(tmp_path):
    pdf = _make_pdf(tmp_path / "source.pdf", rect=(50, 50, 250, 210), color="red")
    payload = collect_pdf_images(pdf)
    assert payload["openable"] is True
    assert payload["page_count"] == 1
    image = payload["images"][0]
    assert image["digest"]
    assert len(image["thumb"]) == 256
    assert image["render_nonwhite_ratio"] > 0
