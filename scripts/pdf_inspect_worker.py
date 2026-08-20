#!/usr/bin/env python3
"""Inspect PDF text layers with PyMuPDF in the fixed D6 PDF runtime."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz


def substantive_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", text)
    return text.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--include-text", action="store_true")
    args = parser.parse_args()

    try:
        doc = fitz.open(args.input)
    except Exception as exc:
        print(json.dumps({
            "openable": False,
            "encrypted": False,
            "error_code": "DOCUMENT_INTEGRITY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        return 0

    try:
        encrypted = bool(getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False))
        if encrypted:
            print(json.dumps({
                "openable": True,
                "encrypted": True,
                "page_count": len(doc),
                "error_code": "DOCUMENT_INTEGRITY_ERROR",
            }, ensure_ascii=False))
            return 0

        page_char_counts: list[int] = []
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text("text") or ""
            pages_text.append(text)
            page_char_counts.append(len(substantive_text(text)))

        text_pages = [index + 1 for index, count in enumerate(page_char_counts) if count > 0]
        no_text_pages = [index + 1 for index, count in enumerate(page_char_counts) if count == 0]
        total_chars = sum(page_char_counts)
        result = {
            "openable": True,
            "encrypted": False,
            "page_count": len(doc),
            "page_text_char_counts": page_char_counts,
            "text_pages": text_pages,
            "no_text_pages": no_text_pages,
            "total_text_chars": total_chars,
            "classification": "text" if text_pages else "scan",
            "error_code": "" if text_pages else "DOCUMENT_OCR_UNSUPPORTED",
        }
        if args.include_text:
            result["pages_text"] = pages_text
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        doc.close()


if __name__ == "__main__":
    raise SystemExit(main())
