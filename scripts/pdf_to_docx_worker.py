#!/usr/bin/env python3
"""Convert a text PDF to DOCX with pdf2docx 0.5.13."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf2docx import Converter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()

    converter = Converter(str(args.input))
    try:
        converter.convert(str(args.output), start=args.start, end=args.end)
    finally:
        converter.close()

    print(json.dumps({
        "input": str(args.input),
        "output": str(args.output),
        "output_size": args.output.stat().st_size if args.output.exists() else 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
