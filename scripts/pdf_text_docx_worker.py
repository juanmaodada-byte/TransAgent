#!/usr/bin/env python3
"""Build a page-ordered text DOCX fallback from a parseable PDF."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


def substantive_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", text)
    return text.strip()


def configure_document(docx: Document) -> None:
    section = docx.sections[0]
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.85)
    section.left_margin = Inches(0.85)
    section.right_margin = Inches(0.85)

    style = docx.styles["Normal"]
    style.font.name = "Noto Sans CJK SC"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "Noto Sans CJK SC")
    paragraph_format = style.paragraph_format
    paragraph_format.line_spacing = 1.15
    paragraph_format.space_after = Pt(5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pdf = fitz.open(args.input)
    try:
        if bool(getattr(pdf, "needs_pass", False) or getattr(pdf, "is_encrypted", False)):
            raise RuntimeError("encrypted PDF is unsupported")

        docx = Document()
        configure_document(docx)
        non_empty_paragraphs = 0
        for page_index, page in enumerate(pdf):
            if page_index:
                docx.add_page_break()
            wrote_on_page = False
            previous_blank = False
            text = page.get_text("text") or ""
            for line in text.splitlines():
                clean_line = line.rstrip()
                if not clean_line and not wrote_on_page:
                    continue
                if not clean_line and previous_blank:
                    continue
                paragraph = docx.add_paragraph(clean_line)
                paragraph.paragraph_format.line_spacing = 1.15
                paragraph.paragraph_format.space_after = Pt(5)
                wrote_on_page = wrote_on_page or bool(clean_line)
                previous_blank = not bool(clean_line)
                if substantive_text(clean_line):
                    non_empty_paragraphs += 1
        if non_empty_paragraphs == 0:
            raise RuntimeError("fallback produced no text paragraphs")

        args.output.parent.mkdir(parents=True, exist_ok=True)
        docx.save(args.output)
        print(json.dumps({
            "output": str(args.output),
            "output_size": args.output.stat().st_size if args.output.exists() else 0,
        }, ensure_ascii=False))
        return 0
    finally:
        pdf.close()


if __name__ == "__main__":
    raise SystemExit(main())
