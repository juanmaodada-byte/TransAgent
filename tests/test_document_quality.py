from __future__ import annotations

import subprocess
import zlib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

from transagent.backend.pipeline import document_quality as quality
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure


def _write_docx(path: Path, text: str = "hello") -> None:
    doc = Document()
    doc.add_paragraph(text)
    doc.save(path)


def _write_png(path: Path, nonblank: bool = True) -> None:
    width = height = 1
    rgb = b"\x00\x00\x00" if nonblank else b"\xff\xff\xff"
    raw = b"\x00" + rgb
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _write_png_rect(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write an RGB PNG of the given size filled entirely with one color."""
    px = bytes(rgb)
    raw = b"".join(b"\x00" + px * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _write_png_bands(path: Path, width: int, height: int, dark_rows: set[int]) -> None:
    """Write an RGB PNG with white background and dark pixels on the given row indices."""
    white = b"\xff\xff\xff"
    dark = b"\x00\x00\x00"
    rows = [bytearray((dark if y in dark_rows else white) * width) for y in range(height)]
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_validate_delivery_docx_success(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    png = tmp_path / "page.png"
    _write_png(png)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "native")
    assert result.page_count == 1
    assert result.blank_pages == []
    assert result.u_fffd_found is False
    assert result.text_image_overlap_count == 0


@pytest.mark.parametrize("fidelity_level", ["native", "normalized", "approximate"])
def test_cjk_rendering_failure_returns_runtime_unavailable_for_all_formats(monkeypatch, tmp_path, fidelity_level):
    docx = tmp_path / "out.docx"
    _write_docx(docx, "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳云计算微服务安全合规")
    png = tmp_path / "page.png"
    _write_png(png)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "云云云云云")
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        quality.validate_delivery_docx(docx, expected, tmp_path, fidelity_level)


def test_cjk_rendering_not_checked_without_cjk(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx, "English only")
    png = tmp_path / "page.png"
    _write_png(png)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "English only")
    monkeypatch.setattr(quality, "_validate_cjk_rendering", lambda *args: (_ for _ in ()).throw(AssertionError("not needed")))
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    assert result.page_count == 1


def test_final_approximate_overlap_blocks_without_output_leak(monkeypatch, tmp_path):
    docx = tmp_path / "secret-output.docx"
    _write_docx(docx)
    png = tmp_path / "page.png"
    _write_png(png)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "rendered.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    monkeypatch.setattr(
        quality,
        "_detect_delivery_text_image_overlap",
        lambda pdf: {"text_image_overlap_count": 2, "text_image_overlap_max_ratio": 0.51},
    )
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR") as err:
        quality.validate_delivery_docx(docx, expected, tmp_path, "approximate")
    assert str(docx) not in str(err.value)
    assert "hello" not in str(err.value)


def test_final_approximate_overlap_metrics_are_public(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    png = tmp_path / "page.png"
    _write_png(png)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "rendered.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    monkeypatch.setattr(
        quality,
        "_detect_delivery_text_image_overlap",
        lambda pdf: {"text_image_overlap_count": 0, "text_image_overlap_max_ratio": 0.0},
    )
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "approximate")
    assert result.to_public_dict()["text_image_overlap_count"] == 0
    assert result.to_public_dict()["text_image_overlap_max_ratio"] == 0.0


@pytest.mark.parametrize(
    ("patch_name", "patch_value", "match"),
    [
        ("render_docx_to_pdf", lambda *args: (_ for _ in ()).throw(quality.quality_error("DOCUMENT_INTEGRITY_ERROR", "output DOCX could not be rendered")), "could not be rendered"),
        ("pdf_page_count", lambda *args: (_ for _ in ()).throw(quality.quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF has zero pages")), "zero pages"),
        ("render_pdf_to_pngs", lambda *args: [], "PNG page count"),
        ("extract_pdf_text", lambda *args: "bad\ufffdtext", "U\\+FFFD"),
    ],
)
def test_delivery_gate_failures(monkeypatch, tmp_path, patch_name, patch_value, match):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    monkeypatch.setattr(quality, patch_name, patch_value)
    with pytest.raises(ValueError, match=match):
        quality.validate_delivery_docx(docx, expected, tmp_path, "native")


def test_single_blank_page_is_rejected(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    blank = tmp_path / "blank.png"
    _write_png(blank, nonblank=False)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [blank])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    with pytest.raises(ValueError, match="blank pages|only blank pages"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "native")


def test_partial_blank_pages_warn_without_blocking(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    blank = tmp_path / "blank.png"
    nonblank = tmp_path / "nonblank.png"
    _write_png(blank, nonblank=False)
    _write_png(nonblank, nonblank=True)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 2)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [nonblank, blank])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "native")
    assert result.blank_pages == [2]
    assert quality.BLANK_PAGE_WARNING in result.warnings


def test_all_blank_pages_are_rejected(monkeypatch, tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    blank1 = tmp_path / "blank1.png"
    blank2 = tmp_path / "blank2.png"
    _write_png(blank1, nonblank=False)
    _write_png(blank2, nonblank=False)
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 2)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [blank1, blank2])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    with pytest.raises(ValueError, match="only blank pages"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "native")


def test_structure_diff_still_blocks(tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    expected = snapshot_docx_structure(str(docx))
    expected["section_count"] += 1
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "native")


def test_render_error_does_not_leak_command_path_or_stderr(monkeypatch, tmp_path):
    docx = tmp_path / "secret-source.docx"
    _write_docx(docx)
    secret = "/secret/internal/path stderr"
    monkeypatch.setattr(quality, "resolve_libreoffice", lambda: SimpleNamespace(executable=Path("/secret/soffice")))
    monkeypatch.setattr(quality.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 9, secret, secret))
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR") as err:
        quality.render_docx_to_pdf(docx, tmp_path)
    assert secret not in str(err.value)
    assert str(docx) not in str(err.value)


def test_invalid_docx_package_rejected(tmp_path):
    bad = tmp_path / "bad.docx"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document xmlns:w='w'>[[TA_X]]</w:document>")
    with pytest.raises(ValueError, match="DOCUMENT_PLACEHOLDER_ERROR"):
        quality.validate_delivery_docx(bad, {}, tmp_path, "native")


# ---------------------------------------------------------------------------
# D10.1 regression tests: CJK render coverage gate
# ---------------------------------------------------------------------------

CJK_12 = "天地玄黄宇宙洪荒日月盈昃"  # 12 unique CJK characters


def test_cjk_render_identical_content_passes(tmp_path):
    docx = tmp_path / "out.docx"
    _write_docx(docx, CJK_12)
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: CJK_12)
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    assert result.page_count == 1
    monkeypatch.undo()


def test_cjk_render_wrong_chars_same_unique_count_rejected(tmp_path):
    # Regression: the old gate compared unique counts and passed a completely different
    # render with the same number of unique CJK characters.
    docx = tmp_path / "out.docx"
    _write_docx(docx, CJK_12)
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "甲乙丙丁戊己庚辛壬癸子丑")
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    monkeypatch.undo()


def test_cjk_render_repeated_char_loss_rejected(tmp_path):
    # Counter-weighted coverage: dropping duplicate occurrences must drop coverage.
    docx = tmp_path / "out.docx"
    _write_docx(docx, CJK_12 * 3)  # each unique char appears 3 times
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: CJK_12)  # only 1/3 of occurrences
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    monkeypatch.undo()


def test_cjk_render_minor_noise_passes(tmp_path):
    # 11/12 characters covered is >= 0.90, so small extraction noise is tolerated.
    docx = tmp_path / "out.docx"
    _write_docx(docx, CJK_12)
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: CJK_12[:11])
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    assert result.page_count == 1
    monkeypatch.undo()


def test_cjk_render_below_coverage_floor_rejected(tmp_path):
    # 10/12 = 0.833 < 0.90 must be rejected.
    docx = tmp_path / "out.docx"
    _write_docx(docx, CJK_12)
    expected = snapshot_docx_structure(str(docx))
    png = tmp_path / "page.png"
    _write_png(png)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 1)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [png])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: CJK_12[:10])
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "normalized")
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# D10.1 regression tests: header/footer-only pages must not mask blank bodies
# ---------------------------------------------------------------------------


def test_header_only_page_is_body_blank(tmp_path):
    # Regression: a page with only a header (top 8%) was counted as non-blank by the
    # whole-page check. The body-band check must still classify it as body-blank.
    png = tmp_path / "header_only.png"
    _write_png_bands(png, 100, 100, dark_rows=set(range(8)))
    assert quality.is_png_nonblank(png) is True
    assert quality.is_png_body_nonblank(png) is False


def test_body_content_page_is_body_nonblank(tmp_path):
    png = tmp_path / "body_content.png"
    _write_png_bands(png, 100, 100, dark_rows=set(range(50, 60)))
    assert quality.is_png_nonblank(png) is True
    assert quality.is_png_body_nonblank(png) is True


def test_approximate_mid_document_body_blank_page_blocks(monkeypatch, tmp_path):
    # Page 1 carries only a header (body blank) and is not trailing -> delivery must block.
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    header_only = tmp_path / "page1.png"
    body_content = tmp_path / "page2.png"
    _write_png_bands(header_only, 100, 100, dark_rows=set(range(8)))
    _write_png_bands(body_content, 100, 100, dark_rows=set(range(50, 60)))
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 2)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [header_only, body_content])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    monkeypatch.setattr(quality, "_detect_delivery_text_image_overlap", lambda pdf: {"text_image_overlap_count": 0, "text_image_overlap_max_ratio": 0.0})
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        quality.validate_delivery_docx(docx, expected, tmp_path, "approximate")


def test_approximate_trailing_body_blank_page_warns(monkeypatch, tmp_path):
    # Page 2 (trailing) carries only a header -> warning, not a block.
    docx = tmp_path / "out.docx"
    _write_docx(docx)
    body_content = tmp_path / "page1.png"
    header_only = tmp_path / "page2.png"
    _write_png_bands(body_content, 100, 100, dark_rows=set(range(50, 60)))
    _write_png_bands(header_only, 100, 100, dark_rows=set(range(8)))
    expected = snapshot_docx_structure(str(docx))
    monkeypatch.setattr(quality, "render_docx_to_pdf", lambda docx_path, out_dir: tmp_path / "out.pdf")
    monkeypatch.setattr(quality, "pdf_page_count", lambda pdf: 2)
    monkeypatch.setattr(quality, "render_pdf_to_pngs", lambda pdf, out_dir, pages: [body_content, header_only])
    monkeypatch.setattr(quality, "extract_pdf_text", lambda pdf, out_dir: "hello")
    monkeypatch.setattr(quality, "_detect_delivery_text_image_overlap", lambda pdf: {"text_image_overlap_count": 0, "text_image_overlap_max_ratio": 0.0})
    result = quality.validate_delivery_docx(docx, expected, tmp_path, "approximate")
    assert result.content_blank_pages == [2]
    assert quality.CONTENT_BLANK_PAGE_WARNING in result.warnings


def test_non_trailing_pages_trailing_run_excluded():
    assert quality._non_trailing_pages([], 3) == []
    assert quality._non_trailing_pages([1, 2, 3], 3) == []
    assert quality._non_trailing_pages([3], 3) == []
    assert quality._non_trailing_pages([1], 3) == [1]
    assert quality._non_trailing_pages([1, 2], 3) == [1, 2]
    assert quality._non_trailing_pages([1, 3], 3) == [1]


# ---------------------------------------------------------------------------
# D10.1 regression tests: image visibility gate
# ---------------------------------------------------------------------------


def test_image_visibility_blocks_single_figure_loss(tmp_path, monkeypatch):
    # Layer 2: one figure in the baseline stays but a second disappears; the gate must
    # reject even though "global" content is mostly still present.
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    _write_png_rect(baseline_dir / "render_page-1.png", 400, 200, (255, 255, 255))
    (baseline_dir / "render.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
    final_pdf = tmp_path / "final.pdf"
    final_pdf.write_bytes(b"%PDF-1.7\n%%EOF")
    monkeypatch.setattr(
        quality,
        "check_pdf_image_visibility",
        lambda ref, cand: {
            "image_visibility_checked": True,
            "meaningful_image_count": 2,
            "matched_visible_image_count": 1,
            "minimum_visible_area_ratio": 1.0,
            "invisible_image_count": 1,
        },
    )
    with pytest.raises(ValueError, match="DOCUMENT_INTEGRITY_ERROR"):
        quality._validate_image_visibility(baseline_dir, final_pdf_path=final_pdf)


def test_image_visibility_passes_when_all_figures_kept(tmp_path, monkeypatch):
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    _write_png_rect(baseline_dir / "render_page-1.png", 400, 200, (255, 255, 255))
    (baseline_dir / "render.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
    final_pdf = tmp_path / "final.pdf"
    final_pdf.write_bytes(b"%PDF-1.7\n%%EOF")
    monkeypatch.setattr(
        quality,
        "check_pdf_image_visibility",
        lambda ref, cand: {
            "image_visibility_checked": True,
            "meaningful_image_count": 2,
            "matched_visible_image_count": 2,
            "minimum_visible_area_ratio": 0.98,
            "invisible_image_count": 0,
        },
    )
    metrics = quality._validate_image_visibility(baseline_dir, final_pdf_path=final_pdf)
    assert metrics["image_visibility_checked"] is True
    assert metrics["meaningful_image_count"] == 2
    assert metrics["matched_visible_image_count"] == 2
    assert metrics["invisible_image_count"] == 0
    assert metrics["minimum_visible_area_ratio"] == 0.98


def test_image_visibility_skipped_without_baseline(tmp_path):
    metrics = quality._validate_image_visibility(None, final_pdf_path=tmp_path / "final.pdf")
    assert metrics["image_visibility_checked"] is False
    assert metrics["invisible_image_count"] == 0


def test_image_visibility_skipped_without_final_render_pdf(tmp_path):
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    _write_png_rect(baseline_dir / "render_page-1.png", 400, 200, (255, 255, 255))
    (baseline_dir / "render.pdf").write_bytes(b"%PDF-1.7\n%%EOF")
    metrics = quality._validate_image_visibility(baseline_dir, final_pdf_path=None)
    assert metrics["image_visibility_checked"] is False


def test_image_visibility_skipped_when_baseline_dir_has_no_render_pdf(tmp_path):
    # A baseline directory with only PNGs (no render PDF) cannot anchor Layer 2.
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    _write_png_rect(baseline_dir / "render_page-1.png", 400, 200, (255, 255, 255))
    final_pdf = tmp_path / "final.pdf"
    final_pdf.write_bytes(b"%PDF-1.7\n%%EOF")
    metrics = quality._validate_image_visibility(baseline_dir, final_pdf_path=final_pdf)
    assert metrics["image_visibility_checked"] is False
