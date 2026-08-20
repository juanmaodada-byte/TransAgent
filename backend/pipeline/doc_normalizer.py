"""DOC normalization through LibreOffice.

This module only converts trusted Word 97-2003 DOC input to a validated DOCX
skeleton for the native document pipeline. It does not translate content.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.backend.pipeline.preprocess import detect_format
from transagent.interface import FormatType


ROOT = Path(__file__).resolve().parents[2]
PROJECT_SOFFICE_CANDIDATES = (
    ROOT / ".runtime" / "libreoffice" / "program" / "soffice",
    ROOT / ".runtime" / "libreoffice" / "soffice",
)
CONVERT_TIMEOUT_SECONDS = 120
VERSION_TIMEOUT_SECONDS = 15
MAX_NORMALIZED_DOCX_BYTES = 200 * 1024 * 1024
DOC_NORMALIZATION_WARNING = (
    "DOC was normalized to DOCX by LibreOffice; minor layout differences may exist."
)


class DocNormalizerError(ValueError):
    """DOC normalizer error with a stable DOCUMENT_* prefix."""


@dataclass
class LibreOfficeInfo:
    executable: Path
    version: str


@dataclass
class NormalizedDocx:
    path: Path
    libreoffice_executable: Path
    libreoffice_version: str
    warnings: list[str]


def normalizer_error(code: str, detail: str) -> DocNormalizerError:
    return DocNormalizerError(f"{code}: {detail}")


def convert_doc_to_docx(source_doc: Path, work_dir: Path) -> Path:
    """Convert a real DOC file to a validated DOCX in ``work_dir``."""
    return normalize_doc_to_docx(source_doc, work_dir).path


def normalize_doc_to_docx(source_doc: Path, work_dir: Path) -> NormalizedDocx:
    """Convert DOC to DOCX with an isolated LibreOffice profile."""
    source_doc = source_doc.resolve()
    work_dir = work_dir.resolve()
    if not work_dir.exists() or not work_dir.is_dir():
        raise normalizer_error("DOCUMENT_CONVERSION_ERROR", "normalization work directory is unavailable")

    fmt = detect_format(str(source_doc))
    if fmt.format_type != FormatType.DOC.value:
        raise normalizer_error("DOCUMENT_FORMAT_MISMATCH", "source is not a verified Word DOC")

    lo = resolve_libreoffice()
    output_dir = work_dir
    profile_dir = work_dir / "libreoffice-profile"
    profile_dir.mkdir(parents=True, exist_ok=False)
    if not profile_dir.resolve().is_relative_to(work_dir):
        raise normalizer_error("DOCUMENT_CONVERSION_ERROR", "LibreOffice profile is outside work directory")

    output_path = work_dir / f"{source_doc.stem}.docx"
    if output_path.exists():
        raise normalizer_error("DOCUMENT_CONVERSION_ERROR", "normalized DOCX output already exists")

    cmd = build_convert_command(lo.executable, source_doc, output_dir, profile_dir)
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=CONVERT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise normalizer_error("DOCUMENT_CONVERSION_ERROR", "LibreOffice DOC conversion timed out") from exc
    except OSError as exc:
        raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice could not be started") from exc

    if result.returncode != 0:
        raise normalizer_error("DOCUMENT_CONVERSION_ERROR", "LibreOffice DOC conversion failed")
    validate_normalized_docx(output_path)
    if output_path.resolve() == source_doc.resolve():
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalization would overwrite source DOC")
    return NormalizedDocx(
        path=output_path,
        libreoffice_executable=lo.executable,
        libreoffice_version=lo.version,
        warnings=[DOC_NORMALIZATION_WARNING],
    )


def build_convert_command(
    soffice: Path,
    source_doc: Path,
    output_dir: Path,
    profile_dir: Path,
) -> list[str]:
    """Build the fixed LibreOffice DOC->DOCX command."""
    return [
        str(soffice),
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--convert-to",
        "docx",
        "--outdir",
        str(output_dir.resolve()),
        str(source_doc.resolve()),
    ]


def resolve_libreoffice() -> LibreOfficeInfo:
    """Resolve LibreOffice from trusted project/runtime locations."""
    candidates: list[Path] = []
    configured = os.environ.get("SOFFICE_PATH")
    if configured:
        path = Path(configured).expanduser()
        if _is_executable_file(path):
            version = libreoffice_version(path)
            return LibreOfficeInfo(executable=path.resolve(), version=version)
        raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice runtime is unavailable")
    candidates.extend(PROJECT_SOFFICE_CANDIDATES)
    found = shutil.which("soffice")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        path = candidate.expanduser()
        if _is_executable_file(path):
            version = libreoffice_version(path)
            return LibreOfficeInfo(executable=path.resolve(), version=version)
    raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice runtime is unavailable")


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def libreoffice_version(soffice: Path) -> str:
    try:
        result = subprocess.run(
            [str(soffice), "--version"],
            text=True,
            capture_output=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice version check timed out") from exc
    except OSError as exc:
        raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice could not be started") from exc
    if result.returncode != 0:
        raise normalizer_error("DOCUMENT_RUNTIME_UNAVAILABLE", "LibreOffice version check failed")
    version = (result.stdout or result.stderr).strip().splitlines()
    return version[0] if version else "unknown"


def validate_normalized_docx(path: Path) -> None:
    """Validate normalized DOCX package integrity before Okapi sees it."""
    if not path.exists():
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "LibreOffice did not create DOCX output")
    if path.stat().st_size == 0:
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized DOCX is empty")
    if path.stat().st_size > MAX_NORMALIZED_DOCX_BYTES:
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized DOCX exceeds size limit")
    fmt = detect_format(str(path))
    if fmt.format_type != FormatType.DOCX.value:
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized output is not a verified DOCX")
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized DOCX ZIP CRC failed")
            names = set(zf.namelist())
            required = {"[Content_Types].xml", "word/document.xml"}
            if required - names:
                raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized DOCX is missing required Word parts")
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    ET.fromstring(zf.read(name))
    except DocNormalizerError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        raise normalizer_error("DOCUMENT_INTEGRITY_ERROR", "normalized DOCX package is invalid") from exc
