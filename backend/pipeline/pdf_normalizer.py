"""Production PDF normalization to approximate DOCX.

The document pipeline consumes only DOCX skeletons. This module verifies a real
PDF, converts it through the fixed project-local PDF runtime, and returns a
validated approximate DOCX. It does not translate content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.backend.pipeline.preprocess import detect_format
from transagent.backend.pipeline.docx_pdf_layout import make_pdf_drawings_inline
from transagent.interface import FormatType


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF_RUNTIME = ROOT / ".runtime" / "pdf" / "venv" / "bin" / "python"
PDF_INSPECT_WORKER = ROOT / "scripts" / "pdf_inspect_worker.py"
PDF_TO_DOCX_WORKER = ROOT / "scripts" / "pdf_to_docx_worker.py"
PDF_TEXT_DOCX_WORKER = ROOT / "scripts" / "pdf_text_docx_worker.py"
PDF_IMAGE_VISIBILITY_WORKER = ROOT / "scripts" / "pdf_image_visibility_worker.py"

# Single source of truth for every production PDF worker that the runtime stage must ship.
# `ensure_pdf_runtime` and `document_runtime_health` both verify against this tuple, and the
# Docker static test scans the `*_WORKER` constants to assert each is COPY'd into the image.
PRODUCTION_PDF_WORKERS = (
    PDF_INSPECT_WORKER,
    PDF_TO_DOCX_WORKER,
    PDF_TEXT_DOCX_WORKER,
    PDF_IMAGE_VISIBILITY_WORKER,
)
MAX_INPUT_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_OUTPUT_DOCX_BYTES = 200 * 1024 * 1024
INSPECTION_TIMEOUT_SECONDS = 60
CONVERSION_TIMEOUT_SECONDS = 180
FALLBACK_TIMEOUT_SECONDS = 120
PDF_APPROXIMATE_WARNING = (
    "PDF was converted to DOCX approximately; layout, reading order, tables, and images may differ."
)
MIXED_TEXT_WARNING = (
    "PDF contains pages without extractable text; image-only page content is not translated."
)
FALLBACK_WARNING = (
    "pdf2docx conversion failed; delivered page-ordered text DOCX with layout and non-text content loss."
)


class PdfNormalizerError(ValueError):
    """Production PDF normalizer error with a stable DOCUMENT_* prefix."""


@dataclass
class PdfInspection:
    openable: bool
    encrypted: bool
    page_count: int = 0
    page_text_char_counts: list[int] = field(default_factory=list)
    text_pages: list[int] = field(default_factory=list)
    no_text_pages: list[int] = field(default_factory=list)
    total_text_chars: int = 0
    classification: str = ""
    error_code: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizedPdfDocx:
    path: Path
    engine: str
    fallback_used: bool
    page_count: int
    text_pages: list[int]
    no_text_pages: list[int]
    total_text_chars: int
    warnings: list[str]
    runtime_version: str
    runtime: dict = field(default_factory=dict)
    readability: dict = field(default_factory=dict)
    source_sha256: str = ""
    normalized_docx_sha256: str = ""


def pdf_normalizer_error(code: str, detail: str) -> PdfNormalizerError:
    return PdfNormalizerError(f"{code}: {detail}")


def normalize_pdf_to_docx(source_pdf: Path, work_dir: Path) -> NormalizedPdfDocx:
    """Convert a verified PDF to an approximate, validated DOCX skeleton."""
    source_pdf = source_pdf.resolve()
    work_dir = work_dir.resolve()
    if not work_dir.exists() or not work_dir.is_dir():
        raise pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF normalization work directory is unavailable")

    fmt = detect_format(str(source_pdf))
    if fmt.format_type != FormatType.PDF.value:
        raise pdf_normalizer_error("DOCUMENT_FORMAT_MISMATCH", "source is not a verified PDF")
    if fmt.size_bytes > MAX_INPUT_PDF_BYTES:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF exceeds input size limit")

    runtime = ensure_pdf_runtime()
    inspection = inspect_pdf(source_pdf)
    _require_convertible_inspection(inspection)

    warnings = [PDF_APPROXIMATE_WARNING, *inspection.warnings]
    output_docx = work_dir / _safe_output_name(source_pdf.name)
    if output_docx.resolve() == source_pdf.resolve():
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF normalization would overwrite source PDF")

    fallback_used = False
    engine = "pdf2docx 0.5.13"
    try:
        _convert_with_pdf2docx(source_pdf, output_docx)
        layout_fix = make_pdf_drawings_inline(output_docx)
    except PdfNormalizerError as exc:
        if not _is_conversion_failure(exc):
            raise
        fallback_used = True
        engine = "pymupdf text -> python-docx"
        warnings.append(FALLBACK_WARNING)
        _fallback_text_docx(source_pdf, output_docx)
        layout_fix = None

    validate_docx_package(output_docx)
    readability = _audit_pdf_readability(source_pdf, output_docx, work_dir)
    warnings.extend(readability.warnings)
    readability_metadata = dict(readability.metadata)
    if layout_fix is not None:
        readability_metadata["pdf_layout_fix"] = layout_fix.to_public_dict()
    normalized_sha = sha256_file(output_docx)
    return NormalizedPdfDocx(
        path=output_docx,
        engine=engine,
        fallback_used=fallback_used,
        page_count=inspection.page_count,
        text_pages=list(inspection.text_pages),
        no_text_pages=list(inspection.no_text_pages),
        total_text_chars=inspection.total_text_chars,
        warnings=warnings,
        runtime_version=str(runtime.get("python", "")),
        runtime=_public_runtime_metadata(runtime),
        readability=readability_metadata,
        source_sha256=sha256_file(source_pdf),
        normalized_docx_sha256=normalized_sha,
    )


def ensure_pdf_runtime() -> dict:
    runtime = resolve_pdf_runtime()
    if any(not worker.exists() or worker.stat().st_size == 0 for worker in PRODUCTION_PDF_WORKERS):
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF worker is unavailable")

    script = (
        "import importlib.metadata as md, sys, json\n"
        "import fitz\n"
        "packages=['pdf2docx','PyMuPDF','python-docx']\n"
        "print(json.dumps({\n"
        " 'python': sys.version.split()[0],\n"
        " 'packages': {name: md.version(name) for name in packages},\n"
        " 'fitz_version': fitz.version,\n"
        "}))\n"
    )
    try:
        result = subprocess.run(
            [str(runtime), "-c", script],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime version check failed") from exc
    if result.returncode != 0:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime version check failed")
    try:
        info = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime version check failed") from exc
    packages = info.get("packages") or {}
    if packages.get("PyMuPDF") != "1.28.2" or packages.get("pdf2docx") != "0.5.13" or not packages.get("python-docx"):
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime package versions are unsupported")
    info["executable"] = str(runtime)
    return info


def inspect_pdf(source_pdf: Path) -> PdfInspection:
    if source_pdf.stat().st_size > MAX_INPUT_PDF_BYTES:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF exceeds input size limit")
    cmd = build_inspect_command(source_pdf)
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=INSPECTION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF text-layer inspection timed out") from exc
    except OSError as exc:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF inspection runtime could not be started") from exc
    if result.returncode != 0:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF text-layer inspection failed")
    try:
        raw = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF inspection worker returned invalid JSON") from exc

    warnings: list[str] = []
    text_pages = list(raw.get("text_pages") or [])
    no_text_pages = list(raw.get("no_text_pages") or [])
    classification = str(raw.get("classification") or "")
    if raw.get("openable") and not raw.get("encrypted") and text_pages and no_text_pages:
        classification = "mixed"
        warnings.append(MIXED_TEXT_WARNING)
    return PdfInspection(
        openable=bool(raw.get("openable")),
        encrypted=bool(raw.get("encrypted")),
        page_count=int(raw.get("page_count") or 0),
        page_text_char_counts=list(raw.get("page_text_char_counts") or []),
        text_pages=text_pages,
        no_text_pages=no_text_pages,
        total_text_chars=int(raw.get("total_text_chars") or 0),
        classification=classification,
        error_code=str(raw.get("error_code") or ""),
        warnings=warnings,
    )


def build_inspect_command(source_pdf: Path) -> list[str]:
    return [str(resolve_pdf_runtime()), str(PDF_INSPECT_WORKER), "--input", str(source_pdf)]


def build_convert_command(source_pdf: Path, output_docx: Path) -> list[str]:
    return [
        str(resolve_pdf_runtime()),
        str(PDF_TO_DOCX_WORKER),
        "--input",
        str(source_pdf),
        "--output",
        str(output_docx),
    ]


def build_fallback_command(source_pdf: Path, output_docx: Path) -> list[str]:
    return [
        str(resolve_pdf_runtime()),
        str(PDF_TEXT_DOCX_WORKER),
        "--input",
        str(source_pdf),
        "--output",
        str(output_docx),
    ]


def validate_docx_package(docx_path: Path) -> dict:
    if not docx_path.exists():
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid")
    size = docx_path.stat().st_size
    if size <= 0 or size > MAX_OUTPUT_DOCX_BYTES:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid")
    if not zipfile.is_zipfile(docx_path):
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid")
    xml_files: list[str] = []
    rels_files: list[str] = []
    try:
        with zipfile.ZipFile(docx_path) as zf:
            bad = zf.testzip()
            if bad:
                raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid")
            names = set(zf.namelist())
            if {"[Content_Types].xml", "word/document.xml"} - names:
                raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid")
            for name in sorted(names):
                if name.endswith(".xml") or name.endswith(".rels"):
                    raw = zf.read(name)
                    ET.fromstring(raw)
                    if name.endswith(".xml"):
                        xml_files.append(name)
                    else:
                        rels_files.append(name)
    except PdfNormalizerError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "converted DOCX is invalid") from exc
    return {"size_bytes": size, "xml_file_count": len(xml_files), "rels_file_count": len(rels_files)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _convert_with_pdf2docx(source_pdf: Path, output_docx: Path) -> None:
    if output_docx.exists():
        output_docx.unlink()
    cmd = build_convert_command(source_pdf, output_docx)
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=CONVERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF to DOCX conversion timed out") from exc
    except OSError as exc:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF conversion runtime could not be started") from exc
    if result.returncode != 0:
        raise pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF to DOCX conversion failed")
    validate_docx_package(output_docx)


def _fallback_text_docx(source_pdf: Path, output_docx: Path) -> None:
    if output_docx.exists():
        output_docx.unlink()
    cmd = build_fallback_command(source_pdf, output_docx)
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=FALLBACK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF fallback conversion timed out") from exc
    except OSError as exc:
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF fallback runtime could not be started") from exc
    if result.returncode != 0:
        raise pdf_normalizer_error("DOCUMENT_CONVERSION_ERROR", "PDF fallback conversion failed")
    validate_docx_package(output_docx)


def _require_convertible_inspection(inspection: PdfInspection) -> None:
    if not inspection.openable:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF is damaged or cannot be parsed")
    if inspection.encrypted:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "encrypted PDF is unsupported")
    if inspection.page_count <= 0:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF has no pages")
    if inspection.page_count > MAX_PDF_PAGES:
        raise pdf_normalizer_error("DOCUMENT_INTEGRITY_ERROR", "PDF exceeds page limit")
    if not inspection.text_pages or inspection.total_text_chars <= 0:
        raise pdf_normalizer_error("DOCUMENT_OCR_UNSUPPORTED", "PDF has no extractable text layer")


def resolve_pdf_runtime() -> Path:
    """Resolve the PDF Python runtime, failing closed for invalid explicit config."""
    configured = os.environ.get("PDF_RUNTIME_PYTHON")
    if configured:
        path = Path(configured).expanduser()
        if _is_executable_file(path):
            return path.absolute()
        raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime is unavailable")
    if _is_executable_file(DEFAULT_PDF_RUNTIME):
        return DEFAULT_PDF_RUNTIME.absolute()
    raise pdf_normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "PDF runtime is unavailable")


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _safe_output_name(name: str) -> str:
    stem = Path(name).stem or "source"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", stem).strip("._")
    return f"{safe or 'source'}-pdf-normalized.docx"


def _is_conversion_failure(exc: PdfNormalizerError) -> bool:
    return str(exc).startswith(("DOCUMENT_CONVERSION_ERROR:", "DOCUMENT_INTEGRITY_ERROR: converted DOCX is invalid"))


def _public_runtime_metadata(runtime: dict) -> dict:
    packages = runtime.get("packages") or {}
    return {
        "python": runtime.get("python", ""),
        "pymupdf": packages.get("PyMuPDF", ""),
        "pdf2docx": packages.get("pdf2docx", ""),
    }


def _audit_pdf_readability(source_pdf: Path, output_docx: Path, work_dir: Path):
    from transagent.backend.pipeline.pdf_readability import audit_pdf_readability

    return audit_pdf_readability(source_pdf, output_docx, work_dir)
