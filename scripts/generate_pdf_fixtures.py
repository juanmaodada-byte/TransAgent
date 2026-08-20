#!/usr/bin/env python3
"""Generate deterministic local PDF fixtures for D6."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
PDF_DIR = FIXTURES / "pdf"
SOFFICE = Path(os.environ.get("SOFFICE_PATH") or shutil.which("soffice") or "soffice")


def draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, width_chars: int = 82, leading: int = 14) -> float:
    words = text.split(" ")
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) > width_chars:
            c.drawString(x, y, line)
            y -= leading
            line = word
        else:
            line = candidate
    if line:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_text_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setTitle("D6 Plain Text PDF")
    for page in range(1, 3):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 730, "D6 Plain Text PDF Probe")
        c.setFont("Helvetica", 10)
        y = 700
        paragraphs = [
            "This fixture checks ordinary text extraction from a born-digital PDF. It includes multiple paragraphs so the conversion has real reading content.",
            "Project URL: https://example.com/transagent/d6/pdf-probe?case=plain",
            "Command: python3 -m pytest tests/test_pdf_probe.py -q",
            f"Page {page} repeats enough text to make the page non-empty and easy to inspect after LibreOffice rendering.",
        ]
        for paragraph in paragraphs:
            y = draw_wrapped(c, paragraph, 72, y)
            y -= 8
        c.showPage()
    c.save()


def build_double_column_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setTitle("D6 Double Column PDF")
    c.setFont("Helvetica-Bold", 15)
    c.drawString(72, 730, "D6 Double Column Reading Order Probe")
    c.setFont("Helvetica", 9)
    left = [
        "Left column paragraph A describes initialization order and expected first-column reading.",
        "Left column paragraph B includes api/v1/resources and stable technical tokens.",
        "Left column paragraph C ends before the right column should begin.",
    ]
    right = [
        "Right column paragraph A should follow the left column in a human reading order.",
        "Right column paragraph B includes kubectl get pods and https://example.com/docs.",
        "Right column paragraph C makes ordering issues visible during manual review.",
    ]
    y_left = 690
    for paragraph in left:
        y_left = draw_wrapped(c, paragraph, 72, y_left, width_chars=42, leading=13)
        y_left -= 8
    y_right = 690
    for paragraph in right:
        y_right = draw_wrapped(c, paragraph, 324, y_right, width_chars=42, leading=13)
        y_right -= 8
    c.showPage()
    c.save()


def build_scanned_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setFillColor(colors.lightgrey)
    c.rect(72, 500, 440, 120, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 18)
    # Text is painted into an image-only page by embedding a generated bitmap-like form.
    png = BytesIO(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    c.drawImage(ImageReader(png), 96, 524, width=380, height=72)
    c.showPage()
    c.save()


def build_empty_page_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.showPage()
    c.save()


def build_mixed_pdf(path: Path) -> None:
    source = FIXTURES / "okapi_probe_mixed.docx"
    with tempfile.TemporaryDirectory(prefix="d6-mixed-pdf-") as tmp:
        profile = Path(tmp) / "profile"
        outdir = Path(tmp) / "out"
        profile.mkdir()
        outdir.mkdir()
        cmd = [
            str(SOFFICE),
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(source),
        ]
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=90, check=False)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip())
        generated = outdir / "okapi_probe_mixed.pdf"
        shutil.copy2(generated, path)


def build_mixed_text_scan_pdf(path: Path, text_pdf: Path, scan_pdf: Path) -> None:
    writer = PdfWriter()
    for source in (text_pdf, scan_pdf):
        reader = PdfReader(str(source))
        writer.add_page(reader.pages[0])
    with path.open("wb") as f:
        writer.write(f)


def build_encrypted_pdf(path: Path, text_pdf: Path) -> None:
    reader = PdfReader(str(text_pdf))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("transagent-d6")
    with path.open("wb") as f:
        writer.write(f)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    text_pdf = PDF_DIR / "d6_plain_text.pdf"
    mixed_pdf = PDF_DIR / "d6_mixed_layout.pdf"
    columns_pdf = PDF_DIR / "d6_double_column.pdf"
    scanned_pdf = PDF_DIR / "d6_scanned_image_only.pdf"
    hybrid_pdf = PDF_DIR / "d6_mixed_text_and_scan.pdf"
    encrypted_pdf = PDF_DIR / "d6_encrypted.pdf"
    empty_pdf = PDF_DIR / "d6_empty_page.pdf"

    build_text_pdf(text_pdf)
    build_double_column_pdf(columns_pdf)
    build_scanned_pdf(scanned_pdf)
    build_empty_page_pdf(empty_pdf)
    build_mixed_pdf(mixed_pdf)
    build_mixed_text_scan_pdf(hybrid_pdf, text_pdf, scanned_pdf)
    build_encrypted_pdf(encrypted_pdf, text_pdf)
    (PDF_DIR / "d6_corrupt_header.pdf").write_bytes(b"%PDF-1.7\nthis is not a valid xref table\n")
    (PDF_DIR / "d6_disguised_text.pdf").write_text("not actually a PDF", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
