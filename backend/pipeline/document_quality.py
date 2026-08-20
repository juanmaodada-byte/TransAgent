"""Production delivery quality gates for DOCX artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import os
import re
import shutil
import subprocess
import tempfile
import zlib
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.backend.pipeline.doc_normalizer import resolve_libreoffice
from transagent.backend.pipeline.docx_cjk_fonts import font_available
from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure
from transagent.backend.pipeline.pdf_image_visibility import check_pdf_image_visibility
from transagent.backend.pipeline.pdf_normalizer import PRODUCTION_PDF_WORKERS, resolve_pdf_runtime


RENDER_TIMEOUT_SECONDS = 90
MAX_OUTPUT_DOCX_BYTES = 200 * 1024 * 1024
BLANK_PAGE_WARNING = "Rendered DOCX contains blank pages; verify intentional section or pagination breaks."
CONTENT_BLANK_PAGE_WARNING = (
    "Rendered DOCX contains page(s) with blank body content (only headers/footers/page numbers); "
    "verify intentional section or pagination breaks."
)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# CJK render gate: the rendered text must cover at least this fraction of the DOCX CJK
# characters (weighted by occurrence count, via Counter). 0.90 tolerates small extraction
# noise (a few glyphs lost by pdftotext) while rejecting wrong-glyph or missing-CJK renders.
CJK_RENDER_COVERAGE_MIN = 0.90
# Belt-and-suspenders length floor: a working CJK render must yield at least half the DOCX
# CJK character count. Never reached when coverage passes, kept as an explicit length check.
CJK_RENDER_LENGTH_FLOOR = 0.50

# Blank-body-page detection excludes stable header/footer/page-number bands near the page
# edges. 12% top + 12% bottom leaves a wide content band for common page geometries.
BODY_BAND = 0.12

# Two-layer image visibility gate. Layer 1 (pdf_readability) verifies that every
# meaningful source PDF figure survives PDF normalization; a lost figure blocks
# extraction. Layer 2 (here) uses that validated normalized render as the baseline and
# matches it against the final delivery render; a single lost figure blocks delivery even
# if global colored-pixel totals rise. Matching is content-level, not geometry (see
# pdf_image_visibility.py): identity is decided by the decoded-pixel digest plus a 16x16
# absolute-grayscale-thumbnail L1 distance, and the candidate must additionally render
# non-white within its placed bbox; geometry (aspect / placed area) is diagnostic only.


class DocumentQualityError(ValueError):
    """Stable DOCUMENT_* delivery quality error."""


@dataclass
class DocumentQualityResult:
    output_path: str
    rendered_pdf_path: str
    rendered_png_paths: list[str]
    page_count: int
    blank_pages: list[int]
    u_fffd_found: bool
    structure_diffs: dict
    text_image_overlap_count: int = 0
    text_image_overlap_max_ratio: float = 0.0
    content_blank_pages: list[int] = field(default_factory=list)
    image_visibility: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "blank_pages": list(self.blank_pages),
            "content_blank_pages": list(self.content_blank_pages),
            "u_fffd_found": self.u_fffd_found,
            "structure_diffs": dict(self.structure_diffs),
            "text_image_overlap_count": self.text_image_overlap_count,
            "text_image_overlap_max_ratio": self.text_image_overlap_max_ratio,
            "image_visibility": dict(self.image_visibility),
            "warnings": list(self.warnings),
        }


def quality_error(code: str, detail: str) -> DocumentQualityError:
    return DocumentQualityError(f"{code}: {detail}")


def validate_delivery_docx(
    output_docx: Path,
    expected_structure_snapshot: dict,
    work_dir: Path,
    fidelity_level: str,
    baseline_png_dir: Path | None = None,
) -> DocumentQualityResult:
    """Validate final DOCX package, structure, renderability, pages, images, and glyphs.

    ``baseline_png_dir`` should hold the normalized DOCX page renders (e.g. the
    pdf-readability audit output) plus the render PDF, and must only be provided when
    Layer 1 (normalization image visibility) already passed. When provided, the final
    render's figures are matched per-image against the baseline to reject delivered
    documents whose figures became invisible, severely cropped, or moved off-page.
    """
    output_docx = output_docx.resolve()
    work_dir = work_dir.resolve()
    _validate_docx_package(output_docx)

    after = snapshot_docx_structure(str(output_docx))
    structure_diffs = _compare_snapshots(expected_structure_snapshot, after)
    if structure_diffs:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "merged DOCX structure differs from source skeleton")

    render_dir = work_dir / "delivery-render"
    render_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = render_docx_to_pdf(output_docx, render_dir)
    page_count = pdf_page_count(pdf_path)
    if page_count <= 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF has zero pages")
    pngs = render_pdf_to_pngs(pdf_path, render_dir, page_count)
    if len(pngs) != page_count:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PNG page count does not match PDF page count")
    blank_pages = [index + 1 for index, path in enumerate(pngs) if not is_png_nonblank(path)]
    if len(blank_pages) == page_count:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered DOCX contains only blank pages")
    content_blank_pages = [index + 1 for index, path in enumerate(pngs) if not is_png_body_nonblank(path)]
    if len(content_blank_pages) == page_count:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered DOCX contains only blank or header/footer-only pages")
    rendered_text = extract_pdf_text(pdf_path, render_dir)
    if _docx_cjk_chars(output_docx):
        _validate_cjk_rendering(output_docx, rendered_text)
    u_fffd_found = "\ufffd" in rendered_text
    if u_fffd_found:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered DOCX contains U+FFFD replacement characters")
    overlap_metrics = {"text_image_overlap_count": 0, "text_image_overlap_max_ratio": 0.0}
    if fidelity_level == "approximate":
        overlap_metrics = _detect_delivery_text_image_overlap(pdf_path)
        if overlap_metrics["text_image_overlap_count"] > 0:
            raise quality_error("DOCUMENT_INTEGRITY_ERROR", "final PDF-derived DOCX has text/image overlap")

    image_visibility = _validate_image_visibility(baseline_png_dir, final_pdf_path=pdf_path)

    warnings: list[str] = []
    if blank_pages:
        warnings.append(BLANK_PAGE_WARNING)
    if content_blank_pages:
        warnings.append(CONTENT_BLANK_PAGE_WARNING)
    if fidelity_level == "approximate":
        # Abnormal body-blank pages in a PDF-derived document are a delivery defect: the
        # source PDF page had content, so a mid-document blank body means lost content.
        # A trailing run of body-blank pages (final section/page break) stays a warning.
        non_trailing_blank = _non_trailing_pages(content_blank_pages, page_count)
        if non_trailing_blank:
            pages = ", ".join(str(page) for page in non_trailing_blank)
            raise quality_error(
                "DOCUMENT_INTEGRITY_ERROR",
                f"delivered PDF-derived DOCX has body-blank page(s) {pages} (content lost or page break anomaly)",
            )
        warnings.append("PDF-derived DOCX passed production render gate; pagination may differ from the source PDF.")
    return DocumentQualityResult(
        output_path=str(output_docx),
        rendered_pdf_path=str(pdf_path),
        rendered_png_paths=[str(path) for path in pngs],
        page_count=page_count,
        blank_pages=blank_pages,
        u_fffd_found=u_fffd_found,
        structure_diffs=structure_diffs,
        text_image_overlap_count=overlap_metrics["text_image_overlap_count"],
        text_image_overlap_max_ratio=overlap_metrics["text_image_overlap_max_ratio"],
        content_blank_pages=content_blank_pages,
        image_visibility=image_visibility,
        warnings=warnings,
    )


def render_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path:
    """Render DOCX to PDF through LibreOffice with an isolated profile."""
    try:
        lo = resolve_libreoffice()
    except Exception as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice runtime is unavailable") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="lo-profile-", dir=str(output_dir)))
    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if pdf_path.exists():
        pdf_path.unlink()
    cmd = [
        str(lo.executable),
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir.resolve()),
        str(docx_path.resolve()),
    ]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "output DOCX could not be rendered") from exc
    except OSError as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice runtime is unavailable") from exc
    if result.returncode != 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "output DOCX could not be rendered")
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "output DOCX could not be rendered")
    return pdf_path


def pdf_page_count(pdf_path: Path) -> int:
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF is empty")
    pdfinfo = resolve_poppler_tool("PDFINFO_PATH", "pdfinfo")
    try:
        result = subprocess.run([pdfinfo, str(pdf_path)], text=True, capture_output=True, timeout=30, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "pdfinfo runtime is unavailable") from exc
    if result.returncode != 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF page count could not be read")
    match = re.search(r"^Pages:\s+(\d+)", result.stdout or "", re.MULTILINE)
    pages = int(match.group(1)) if match else 0
    if pages <= 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF has zero pages")
    return pages


def render_pdf_to_pngs(pdf_path: Path, output_dir: Path, expected_pages: int) -> list[Path]:
    pdftoppm = resolve_poppler_tool("PDFTOPPM_PATH", "pdftoppm")
    prefix = output_dir / "render_page"
    for old in output_dir.glob("render_page-*.png"):
        old.unlink()
    try:
        result = subprocess.run([pdftoppm, "-png", str(pdf_path), str(prefix)], text=True, capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "pdftoppm runtime is unavailable") from exc
    if result.returncode != 0:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PDF pages could not be rasterized")
    pngs = sorted(output_dir.glob("render_page-*.png"))
    if len(pngs) != expected_pages:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PNG page count does not match PDF page count")
    return pngs


def extract_pdf_text(pdf_path: Path, output_dir: Path) -> str:
    try:
        pdftotext = resolve_poppler_tool("PDFTOTEXT_PATH", "pdftotext")
    except DocumentQualityError as exc:
        if os.environ.get("PDFTOTEXT_PATH"):
            raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF text extraction runtime is unavailable") from exc
        pdftotext = ""
    text_path = output_dir / "render_text.txt"
    if pdftotext:
        try:
            result = subprocess.run([pdftotext, "-enc", "UTF-8", str(pdf_path), str(text_path)], text=True, capture_output=True, timeout=60, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF text extraction runtime is unavailable") from exc
        if result.returncode == 0 and text_path.exists():
            return text_path.read_text(encoding="utf-8", errors="replace")

    try:
        runtime = resolve_pdf_runtime()
    except Exception as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF text extraction runtime is unavailable")
    script = (
        "from pathlib import Path\n"
        "from pypdf import PdfReader\n"
        "import sys\n"
        "reader=PdfReader(sys.argv[1])\n"
        "Path(sys.argv[2]).write_text('\\n'.join((p.extract_text() or '') for p in reader.pages), encoding='utf-8')\n"
    )
    try:
        result = subprocess.run([str(runtime), "-c", script, str(pdf_path), str(text_path)], text=True, capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF text extraction runtime is unavailable") from exc
    if result.returncode != 0 or not text_path.exists():
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF text extraction runtime is unavailable")
    return text_path.read_text(encoding="utf-8", errors="replace")


def _png_pixels(path: Path):
    """Decode a PNG into rows of 8-bit pixel bytearrays.

    Returns ``None`` when the PNG uses an unsupported pixel format (non-8-bit depth or
    unexpected color type). Raises DOCUMENT_INTEGRITY_ERROR for malformed PNGs.
    """
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered page is not a PNG")
    pos = 8
    width = height = color_type = bit_depth = None
    compressed = b""
    while pos < len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        chunk_type = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            bit_depth = chunk[8]
            color_type = chunk[9]
        elif chunk_type == b"IDAT":
            compressed += chunk
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or bit_depth != 8 or color_type not in {0, 2, 6}:
        return None
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(compressed)
    stride = width * channels
    prev = bytearray(stride)
    offset = 0
    rows = []
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = prev[i]
            upper_left = prev[i - channels] if i >= channels else 0
            if filter_type == 1:
                row[i] = (row[i] + left) & 0xFF
            elif filter_type == 2:
                row[i] = (row[i] + up) & 0xFF
            elif filter_type == 3:
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                row[i] = (row[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else upper_left)) & 0xFF
            elif filter_type != 0:
                raise quality_error("DOCUMENT_INTEGRITY_ERROR", "rendered PNG uses an unsupported filter")
        rows.append(row)
        prev = row
    return width, height, channels, rows


def is_png_nonblank(path: Path) -> bool:
    """True when any pixel on the whole page is not near-white (blank-page check)."""
    decoded = _png_pixels(path)
    if decoded is None:
        return path.stat().st_size > 0
    height, channels, rows = decoded[1], decoded[2], decoded[3]
    for y in range(height):
        row = rows[y]
        for i in range(0, len(row), channels):
            rgb = row[i:i + (1 if channels == 1 else 3)]
            if any(value < 250 for value in rgb):
                return True
    return False


def is_png_body_nonblank(path: Path, band: float = BODY_BAND) -> bool:
    """True when any pixel in the body content band is not near-white.

    The body band excludes stable header/footer/page-number regions near the top and
    bottom edges, so a page carrying only a header (or footer/page number) is treated
    as body-blank.
    """
    decoded = _png_pixels(path)
    if decoded is None:
        return path.stat().st_size > 0
    height, channels, rows = decoded[1], decoded[2], decoded[3]
    band = max(0.0, min(0.49, float(band)))
    start = int(height * band)
    end = int(height * (1.0 - band))
    if end <= start:  # degenerate tiny image: fall back to the whole-page check
        return is_png_nonblank(path)
    for y in range(start, end):
        row = rows[y]
        for i in range(0, len(row), channels):
            rgb = row[i:i + (1 if channels == 1 else 3)]
            if any(value < 250 for value in rgb):
                return True
    return False


def _non_trailing_pages(blank_pages: list[int], page_count: int) -> list[int]:
    """Return blank pages that are not part of a trailing run at the end of the document."""
    if not blank_pages:
        return []
    blank_set = set(blank_pages)
    trailing = set()
    for page in range(page_count, 0, -1):
        if page in blank_set:
            trailing.add(page)
        else:
            break
    return [page for page in sorted(blank_pages) if page not in trailing]


def _sorted_render_pngs(png_dir: Path) -> list[Path]:
    """Return render_page-*.png files sorted numerically by page number."""
    if not png_dir.exists():
        return []
    pngs = [path for path in png_dir.glob("render_page-*.png") if path.is_file()]
    pngs.sort(key=lambda path: _page_number(path.name))
    return pngs


def _page_number(filename: str) -> int:
    match = re.search(r"render_page-(\d+)", filename)
    if match:
        return int(match.group(1))
    return 0


def _validate_image_visibility(
    baseline_png_dir: Path | None,
    *,
    final_pdf_path: Path | None = None,
) -> dict:
    """Layer 2: block delivery when figures present in the baseline render disappear.

    Per-image geometry matching between the normalized baseline render PDF (usable as a
    baseline only after Layer 1 passed, see native_document.merge_translations) and the
    final delivery render PDF. A single invisible figure blocks delivery, even when
    global colored-pixel totals rise. Returns only public non-sensitive metrics.
    """
    metrics = {
        "image_visibility_checked": False,
        "meaningful_image_count": 0,
        "matched_visible_image_count": 0,
        "minimum_visible_area_ratio": 1.0,
        "invisible_image_count": 0,
    }
    if not baseline_png_dir or not final_pdf_path:
        return metrics
    baseline_pdf = _find_render_pdf(Path(baseline_png_dir))
    if baseline_pdf is None:
        return metrics
    try:
        result = check_pdf_image_visibility(baseline_pdf, final_pdf_path)
    except Exception as exc:
        code = "DOCUMENT_INTEGRITY_ERROR"
        if str(exc).startswith("DOCUMENT_RUNTIME_UNAVAILABLE"):
            code = "DOCUMENT_RUNTIME_UNAVAILABLE"
        raise quality_error(code, "final delivery image visibility check failed") from exc
    if result["invisible_image_count"] > 0:
        raise quality_error(
            "DOCUMENT_INTEGRITY_ERROR",
            "delivered DOCX lost visible figure(s) from the normalized baseline "
            f"({result['invisible_image_count']} of {result['meaningful_image_count']} figures invisible; "
            f"matched {result['matched_visible_image_count']})",
        )
    return result


def _find_render_pdf(png_dir: Path) -> Path | None:
    """Locate the render PDF stored next to the baseline page PNGs."""
    pdfs = sorted(path for path in png_dir.glob("*.pdf") if path.is_file())
    return pdfs[0] if pdfs else None


def check_document_runtime_health(require_cjk_font: bool = False) -> dict:
    """Read-only health check for document delivery runtime dependencies."""
    root = Path(__file__).resolve().parents[2]
    health = {
        "java_17": _java17_health(root),
        "tikal_1_48_0": _tikal_health(root),
        "okapi_config": _okapi_config_health(root / "configs" / "okapi" / "openxml_docx_p0.fprm"),
        "libreoffice": _libreoffice_health(),
        "pdf_runtime": _pdf_runtime_health(),
        "pymupdf_1_28_2": _pdf_package_health("PyMuPDF", "1.28.2"),
        "pdf2docx_0_5_13": _pdf_package_health("pdf2docx", "0.5.13"),
        "python_docx": _python_package_health("python-docx"),
        "pdfinfo": _poppler_health("PDFINFO_PATH", "pdfinfo"),
        "pdftoppm": _poppler_health("PDFTOPPM_PATH", "pdftoppm"),
        "pdftotext_or_pypdf": _pdftotext_or_pypdf_health(),
        "cjk_font": _cjk_font_health(),
        "temp_work_dir": _temp_work_dir_health(),
        "pdf_workers": _pdf_workers_health(),
    }
    health["ok"] = all(
        item.get("ok", False)
        for key, item in health.items()
        if require_cjk_font or key != "cjk_font"
    )
    return health


def _validate_docx_package(output_path: Path) -> None:
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise quality_error("DOCUMENT_MERGE_ERROR", "merged DOCX was not created")
    if output_path.stat().st_size > MAX_OUTPUT_DOCX_BYTES:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "merged DOCX exceeds size limit")
    if not zipfile.is_zipfile(output_path):
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "merged output is not a valid DOCX ZIP")
    try:
        with zipfile.ZipFile(output_path) as zf:
            bad = zf.testzip()
            if bad:
                raise quality_error("DOCUMENT_INTEGRITY_ERROR", "merged DOCX ZIP CRC failed")
            for name in zf.namelist():
                if name.endswith(".xml") or name.endswith(".rels"):
                    raw = zf.read(name)
                    ET.fromstring(raw)
                    if b"[[TA_" in raw:
                        raise quality_error("DOCUMENT_PLACEHOLDER_ERROR", "internal placeholder remained in merged DOCX")
    except DocumentQualityError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        raise quality_error("DOCUMENT_INTEGRITY_ERROR", "merged output is not a valid DOCX ZIP") from exc


def _validate_cjk_rendering(output_docx: Path, rendered_text: str) -> None:
    """Verify the rendered text actually contains the DOCX's CJK characters.

    The old gate only compared the number of unique CJK characters, so a render with a
    completely different but equally-sized character set passed. This version computes
    Counter coverage: how much of the DOCX CJK character stream (weighted by occurrence)
    appears in the rendered text. Wrong characters contribute nothing, and characters
    lost wholesale drop the coverage below the threshold.
    """
    docx_counter = _docx_cjk_counter(output_docx)
    if not docx_counter:
        return
    rendered_counter = Counter(CJK_RE.findall(rendered_text))
    total = sum(docx_counter.values())
    covered = sum(min(docx_counter[char], rendered_counter[char]) for char in docx_counter)
    if total == 0:
        return
    coverage = covered / total
    rendered_total = sum(rendered_counter.values())
    if coverage < CJK_RENDER_COVERAGE_MIN or rendered_total < total * CJK_RENDER_LENGTH_FLOOR:
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", "CJK font rendering runtime is unavailable")


def _detect_delivery_text_image_overlap(rendered_pdf: Path) -> dict:
    from transagent.backend.pipeline import pdf_overlap

    return pdf_overlap.detect_rendered_text_image_overlap(rendered_pdf)


def _docx_cjk_counter(output_docx: Path) -> Counter:
    counter: Counter = Counter()
    try:
        with zipfile.ZipFile(output_docx) as zf:
            for name in zf.namelist():
                if name.startswith("word/") and name.endswith(".xml"):
                    text = "".join(ET.fromstring(zf.read(name)).itertext())
                    counter.update(CJK_RE.findall(text))
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError):
        return counter
    return counter


def _docx_cjk_chars(output_docx: Path) -> set[str]:
    return set(_docx_cjk_counter(output_docx))


def _compare_snapshots(before: dict, after: dict) -> dict:
    diffs: dict = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diffs[key] = {"before": before.get(key), "after": after.get(key)}
    return diffs


def _command_version(cmd: list[str], required_fragment: str | None = None) -> dict:
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=15, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "version": ""}
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    lines = text.splitlines()
    version = lines[0] if lines else ""
    if required_fragment:
        version = next((line for line in lines if required_fragment in line), version)
    return {"ok": result.returncode == 0 and (required_fragment is None or required_fragment in text), "version": version}


def _java17_health(root: Path) -> dict:
    configured_home = os.environ.get("JAVA_HOME")
    if configured_home:
        java = Path(configured_home).expanduser() / "bin" / "java"
        if not _is_executable_file(java):
            return {"ok": False, "version": ""}
        health = _command_version([str(java), "-version"], "17")
        health["source"] = "JAVA_HOME"
        return health
    bundled = root / ".runtime" / "java17" / "jdk-17.0.20+8-jre" / "Contents" / "Home" / "bin" / "java"
    candidates = [bundled]
    system_java = shutil.which("java")
    if system_java:
        candidates.append(Path(system_java))
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            health = _command_version([str(candidate), "-version"], "17")
            if health["ok"]:
                health["source"] = "bundled" if candidate == bundled else "system"
                return health
    return _command_version(["java", "-version"], "17")


def _path_exists(path: Path) -> dict:
    return {"ok": path.exists(), "available": path.exists()}


def _tikal_health(root: Path) -> dict:
    configured = os.environ.get("TIKAL_PATH")
    path = Path(configured).expanduser() if configured else root / ".runtime" / "okapi-1.48.0" / "tikal-java17.sh"
    if not _is_executable_file(path):
        return {"ok": False, "available": False, "version": ""}
    health = _command_version([str(path)], "1.48.0")
    health["available"] = True
    return health


def _okapi_config_health(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {"ok": False, "available": False, "sha256": ""}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "available": True, "sha256": ""}
    p0_markers = [
        "bPreferenceTranslateWordHeadersFooters.b=false",
        "bPreferenceTranslateComments.b=false",
        "bPreferenceAggressiveCleanup.b=false",
    ]
    ok = all(marker in text for marker in p0_markers)
    return {"ok": ok, "available": True, "sha256": sha256_file(path)}


def _which_health(name: str) -> dict:
    found = shutil.which(name)
    return {"ok": bool(found), "available": bool(found)}


def resolve_poppler_tool(env_name: str, command_name: str) -> str:
    configured = os.environ.get(env_name)
    if configured:
        path = Path(configured).expanduser()
        if _is_executable_file(path):
            return str(path.resolve())
        raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", f"{command_name} runtime is unavailable")
    found = shutil.which(command_name)
    if found and _is_executable_file(Path(found)):
        return found
    raise quality_error("DOCUMENT_RUNTIME_UNAVAILABLE", f"{command_name} runtime is unavailable")


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _pdf_runtime_health() -> dict:
    try:
        resolve_pdf_runtime()
    except Exception:
        return {"ok": False, "available": False}
    return {"ok": True, "available": True}


def _temp_work_dir_health() -> dict:
    root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="ta-health-", dir=str(root), delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return {"ok": True, "writable": True}
    except OSError:
        return {"ok": False, "writable": False}


def _pdf_workers_health() -> dict:
    """Verify every production PDF worker exists, is a regular readable file, and is non-empty."""
    missing = []
    for worker in PRODUCTION_PDF_WORKERS:
        try:
            ok = worker.exists() and worker.is_file() and worker.stat().st_size > 0 and os.access(worker, os.R_OK)
        except OSError:
            ok = False
        if not ok:
            missing.append(worker.name)
    return {"ok": not missing, "total": len(PRODUCTION_PDF_WORKERS), "missing": missing}


def _poppler_health(env_name: str, command_name: str) -> dict:
    try:
        resolve_poppler_tool(env_name, command_name)
    except DocumentQualityError:
        return {"ok": False, "available": False}
    return {"ok": True, "available": True}


def _pdftotext_or_pypdf_health() -> dict:
    if os.environ.get("PDFTOTEXT_PATH"):
        return _poppler_health("PDFTOTEXT_PATH", "pdftotext")
    return _poppler_health("PDFTOTEXT_PATH", "pdftotext") if shutil.which("pdftotext") else _pdf_package_health("pypdf", None)


def _libreoffice_health() -> dict:
    try:
        lo = resolve_libreoffice()
    except Exception:
        return {"ok": False, "version": ""}
    return {"ok": True, "version": lo.version}


def _python_package_health(package: str) -> dict:
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError:
        return {"ok": False, "version": ""}
    return {"ok": True, "version": version}


def _pdf_package_health(package: str, expected: str | None) -> dict:
    try:
        runtime = resolve_pdf_runtime()
    except Exception:
        return {"ok": False, "version": ""}
    script = f"import importlib.metadata as md; print(md.version({package!r}))"
    try:
        result = subprocess.run([str(runtime), "-c", script], text=True, capture_output=True, timeout=15, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return {"ok": False, "version": ""}
    version = (result.stdout or "").strip()
    return {"ok": result.returncode == 0 and bool(version) and (expected is None or version == expected), "version": version}


def _cjk_font_health() -> dict:
    preferred = ["Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans SC", "Noto Serif SC"]
    fallback = ["Hiragino Sans GB", "STHeiti", "Songti", "Songti SC", "PingFang SC", "Microsoft YaHei", "SimSun", "Source Han Sans SC", "WenQuanYi Zen Hei"]
    for name in preferred:
        if _font_available(name):
            return {"status": "preferred CJK font available", "font": name, "ok": True}
    for name in fallback:
        if _font_available(name):
            return {"status": "fallback font available", "font": name, "ok": True}
    return {"status": "no suitable CJK font", "font": "", "ok": False}


def _font_available(name: str) -> bool:
    return font_available(name)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
