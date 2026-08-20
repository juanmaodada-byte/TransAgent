#!/usr/bin/env python3
"""D6 text-PDF to approximate DOCX Go/No-Go probe."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interface import DocumentBlock
from backend.pipeline.docx_snapshot import snapshot_docx_structure
from backend.pipeline.pdf_probe import (
    FALLBACK_WARNING,
    MIXED_TEXT_WARNING,
    PDF_RUNTIME,
    compare_snapshots,
    convert_pdf_to_docx,
    docx_contains_replacement_char,
    docx_has_residual_placeholders,
    ensure_pdf_fixtures,
    ensure_pdf_runtime,
    fallback_text_docx,
    inspect_pdf,
    pdf_error,
    require_convertible_pdf,
    sha256_file,
    validate_docx_package,
    write_runtime_manifest,
)
from backend.pipeline.xliff_codec import XLIFF_NS, encode_source, inline_signature, namespace_of, qname
from scripts.d2_okapi_probe import (
    DEFAULT_BUNDLED_JAVA,
    DEFAULT_BUNDLED_PYTHON,
    DEFAULT_BUNDLED_TIKAL,
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_ID,
    check_pdf_render,
    create_run_dir,
    extract_with_tikal,
    merge_with_tikal,
    render_docx,
)


RUNS_DIR = ROOT / "runs" / "d6_pdf_probe"
REPORT = ROOT / "D6_PDF_PROBE_REPORT.md"
RUNTIME_MANIFEST = ROOT / "PDF_RUNTIME_MANIFEST.md"
SOFFICE = os.environ.get("SOFFICE_PATH") or shutil.which("soffice")
PDFINFO = shutil.which("pdfinfo")
PDFTOPPM = shutil.which("pdftoppm")
PYTHON_RENDER = str(DEFAULT_BUNDLED_PYTHON)


ALLOWED_STATUS = {"GO", "NO-GO", "BLOCKED_BY_ENVIRONMENT"}


def run_command(cmd: list[str], timeout: int = 30, cwd: Path | None = None) -> dict:
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, cwd=str(cwd) if cwd else None, check=False)
    return {"cmd": cmd, "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}


def blocks_from_xliff(xliff_path: Path) -> list[DocumentBlock]:
    root = ET.parse(xliff_path).getroot()
    namespace = namespace_of(root.tag)
    if namespace != XLIFF_NS:
        raise RuntimeError(f"unsupported XLIFF namespace: {namespace}")
    blocks: list[DocumentBlock] = []
    seen: set[str] = set()
    for file_node in root.findall(qname(namespace, "file")):
        original = file_node.get("original", "")
        for unit in file_node.findall(f".//{qname(namespace, 'trans-unit')}"):
            unit_id = unit.get("id") or ""
            if not unit_id:
                raise RuntimeError("trans-unit missing id")
            if unit_id in seen:
                raise RuntimeError(f"duplicate trans-unit id: {unit_id}")
            seen.add(unit_id)
            source = unit.find(qname(namespace, "source"))
            if source is None:
                raise RuntimeError(f"trans-unit {unit_id} missing source")
            text, metadata = encode_source(source)
            blocks.append(DocumentBlock(
                block_id=unit_id,
                block_type="text",
                text=text,
                order=len(blocks),
                metadata={**metadata, "xliff_file_original": original, "source_inline_signature": inline_signature(source)},
            ))
    if not blocks:
        raise RuntimeError("XLIFF contains zero trans-unit elements")
    return blocks


def pseudo_translate_xliff_from_blocks(xliff_path: Path, blocks: list[DocumentBlock], target_lang: str = "zh-CN") -> None:
    from backend.pipeline.xliff_codec import restore_target

    tree = ET.parse(xliff_path)
    root = tree.getroot()
    namespace = namespace_of(root.tag)
    by_id = {block.block_id: block for block in blocks}
    for unit in root.findall(f".//{qname(namespace, 'trans-unit')}"):
        unit_id = unit.get("id") or ""
        block = by_id[unit_id]
        source = unit.find(qname(namespace, "source"))
        if source is None:
            raise RuntimeError(f"trans-unit {unit_id} missing source")
        target = restore_target(source, f"[D6-ZH] {block.text}", block.metadata, target_lang)
        old_target = unit.find(qname(namespace, "target"))
        if old_target is None:
            unit.insert(list(unit).index(source) + 1, target)
        else:
            idx = list(unit).index(old_target)
            unit.remove(old_target)
            unit.insert(idx, target)
    tree.write(xliff_path, encoding="utf-8", xml_declaration=True)


def okapi_roundtrip(docx_path: Path, run_dir: Path, label: str) -> dict:
    chain_dir = run_dir / f"okapi_{label}"
    chain_dir.mkdir(parents=True)
    run_docx = chain_dir / "converted.docx"
    shutil.copy2(docx_path, run_docx)
    shutil.copy2(DEFAULT_CONFIG, chain_dir / f"{DEFAULT_CONFIG_ID}.fprm")

    before = snapshot_docx_structure(str(run_docx))
    extract = extract_with_tikal(str(DEFAULT_BUNDLED_TIKAL), run_docx, chain_dir, DEFAULT_CONFIG_ID, str(DEFAULT_BUNDLED_JAVA))
    if extract.returncode != 0:
        raise RuntimeError(f"Okapi extract failed: {(extract.stderr or extract.stdout).strip()}")
    xliff = chain_dir / "converted.docx.xlf"
    if not xliff.exists() or xliff.stat().st_size == 0:
        raise RuntimeError("Okapi did not create XLIFF")
    blocks = blocks_from_xliff(xliff)
    if len({block.block_id for block in blocks}) != len(blocks):
        raise RuntimeError("block IDs are not unique")
    pseudo_translate_xliff_from_blocks(xliff, blocks)
    merge = merge_with_tikal(str(DEFAULT_BUNDLED_TIKAL), xliff, chain_dir, DEFAULT_CONFIG_ID, str(DEFAULT_BUNDLED_JAVA))
    if merge.returncode != 0:
        raise RuntimeError(f"Okapi merge failed: {(merge.stderr or merge.stdout).strip()}")
    merged = chain_dir / "converted.docx"
    validate_docx_package(merged)
    after = snapshot_docx_structure(str(merged))
    diffs = compare_snapshots(before, after)
    if docx_has_residual_placeholders(merged):
        raise RuntimeError("merged DOCX contains residual [[TA_*]] placeholders")
    if docx_contains_replacement_char(merged):
        raise RuntimeError("merged DOCX contains U+FFFD")
    return {
        "chain_dir": str(chain_dir),
        "xliff_path": str(xliff),
        "blocks_count": len(blocks),
        "block_ids_unique": True,
        "extract": extract.__dict__,
        "merge": merge.__dict__,
        "merged_docx": str(merged),
        "before_snapshot": before,
        "after_snapshot": after,
        "structure_diffs": diffs,
        "residual_placeholders": False,
        "u_fffd_found": False,
    }


def render_pdf_to_pngs(pdf_path: Path, run_dir: Path, label: str) -> dict:
    out_dir = run_dir / f"visual_{label}_input"
    out_dir.mkdir(parents=True, exist_ok=True)
    return check_pdf_render(pdf_path, out_dir, PDFINFO, PDFTOPPM, None, PYTHON_RENDER)


def render_docx_to_pdf_and_pngs(docx_path: Path, run_dir: Path, label: str) -> dict:
    out_dir = run_dir / f"visual_{label}_docx"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = render_docx(SOFFICE, docx_path, out_dir)
    data = check_pdf_render(pdf, out_dir, PDFINFO, PDFTOPPM, None, PYTHON_RENDER)
    return {"pdf": str(pdf), **data}


def fixture_paths(pdf_dir: Path) -> dict[str, Path]:
    return {
        "plain": pdf_dir / "d6_plain_text.pdf",
        "mixed": pdf_dir / "d6_mixed_layout.pdf",
        "columns": pdf_dir / "d6_double_column.pdf",
        "scan": pdf_dir / "d6_scanned_image_only.pdf",
        "hybrid": pdf_dir / "d6_mixed_text_and_scan.pdf",
        "corrupt": pdf_dir / "d6_corrupt_header.pdf",
        "disguised": pdf_dir / "d6_disguised_text.pdf",
        "encrypted": pdf_dir / "d6_encrypted.pdf",
        "empty": pdf_dir / "d6_empty_page.pdf",
    }


def run_probe(force_fallback: bool = True) -> tuple[str, dict]:
    run_dir = create_run_dir(RUNS_DIR)
    environment_blockers: list[str] = []
    product_blockers: list[str] = []
    warnings: list[str] = []
    data: dict = {
        "run_dir": str(run_dir),
        "fidelity_level": "approximate",
        "environment_blockers": environment_blockers,
        "product_blockers": product_blockers,
        "warnings": warnings,
    }

    try:
        runtime = ensure_pdf_runtime()
        write_runtime_manifest(RUNTIME_MANIFEST, runtime)
        data["runtime"] = runtime
    except Exception as exc:
        environment_blockers.append(str(exc))
        return "BLOCKED_BY_ENVIRONMENT", data

    for name, path in {
        "soffice": SOFFICE,
        "pdfinfo": PDFINFO,
        "pdftoppm": PDFTOPPM,
        "tikal": str(DEFAULT_BUNDLED_TIKAL),
    }.items():
        if not path or not Path(path).exists():
            environment_blockers.append(f"{name} unavailable: {path}")
    if environment_blockers:
        return "BLOCKED_BY_ENVIRONMENT", data

    try:
        pdf_dir = ensure_pdf_fixtures()
        fixtures = fixture_paths(pdf_dir)
        data["fixtures"] = {name: str(path) for name, path in fixtures.items()}

        inspections = {name: asdict(inspect_pdf(path)) for name, path in fixtures.items()}
        data["pdf_inspection"] = inspections
        if inspections["scan"]["error_code"] != "DOCUMENT_OCR_UNSUPPORTED":
            product_blockers.append("scan PDF was not rejected as DOCUMENT_OCR_UNSUPPORTED")
        if inspections["corrupt"]["error_code"] != "DOCUMENT_INTEGRITY_ERROR":
            product_blockers.append("corrupt PDF was not rejected as DOCUMENT_INTEGRITY_ERROR")
        if inspections["disguised"]["error_code"] != "DOCUMENT_INTEGRITY_ERROR":
            product_blockers.append("disguised non-PDF was not rejected as DOCUMENT_INTEGRITY_ERROR")
        if inspections["encrypted"]["error_code"] != "DOCUMENT_INTEGRITY_ERROR":
            product_blockers.append("encrypted PDF was not rejected as DOCUMENT_INTEGRITY_ERROR")
        if inspections["hybrid"]["classification"] == "mixed":
            warnings.extend(inspections["hybrid"]["warnings"])

        conversions: dict[str, dict] = {}
        for label in ("plain", "mixed", "columns"):
            inspection = require_convertible_pdf(fixtures[label])
            out = run_dir / f"{label}_converted.docx"
            conversion = convert_pdf_to_docx(fixtures[label], out)
            package = validate_docx_package(out)
            snapshot = snapshot_docx_structure(str(out))
            input_render = render_pdf_to_pngs(fixtures[label], run_dir, label)
            docx_render = render_docx_to_pdf_and_pngs(out, run_dir, label)
            conversion_warnings = list(inspection.warnings)
            if input_render["pdf_pages"] != docx_render["pdf_pages"]:
                conversion_warnings.append(f"{label}: rendered page count changed from {input_render['pdf_pages']} to {docx_render['pdf_pages']}.")
            if label == "mixed":
                conversion_warnings.append("Mixed layout requires manual visual review for image/table positioning after pdf2docx conversion.")
            if label == "columns":
                conversion_warnings.append("Double-column reading order requires manual review; pdf2docx may interleave column text.")
            warnings.extend(conversion_warnings)
            conversions[label] = {
                "inspection": asdict(inspection),
                "conversion": asdict(conversion),
                "docx_package": package,
                "snapshot": snapshot,
                "converted_docx_size": out.stat().st_size,
                "converted_docx_image_count": snapshot["image_count"],
                "converted_docx_table_count": snapshot["table_count"],
                "input_render": input_render,
                "converted_render": docx_render,
                "warnings": conversion_warnings,
            }

        plain_chain = okapi_roundtrip(Path(conversions["plain"]["conversion"]["output_docx"]), run_dir, "plain")
        conversions["plain"]["okapi"] = plain_chain
        if plain_chain["structure_diffs"]:
            product_blockers.append("plain converted vs merged DOCX structure snapshot changed")

        fallback_out = run_dir / "plain_fallback.docx"
        fallback = fallback_text_docx(fixtures["plain"], fallback_out) if force_fallback else None
        fallback_chain = okapi_roundtrip(fallback_out, run_dir, "fallback")
        if fallback_chain["structure_diffs"]:
            product_blockers.append("fallback converted vs merged DOCX structure snapshot changed")
        fallback_render = render_docx_to_pdf_and_pngs(fallback_out, run_dir, "fallback")
        data["fallback"] = {
            "conversion": asdict(fallback),
            "docx_package": validate_docx_package(fallback_out),
            "snapshot": snapshot_docx_structure(str(fallback_out)),
            "okapi": fallback_chain,
            "render": fallback_render,
        }
        warnings.extend(fallback.warnings)
        data["conversions"] = conversions
    except Exception as exc:
        product_blockers.append(f"{type(exc).__name__}: {exc}")

    if environment_blockers:
        status = "BLOCKED_BY_ENVIRONMENT"
    elif product_blockers:
        status = "NO-GO"
    else:
        status = "GO"
    return status, data


def write_report(path: Path, status: str, data: dict) -> None:
    assert status in ALLOWED_STATUS
    env_lines = [f"- {item}" for item in data.get("environment_blockers", [])] or ["- None"]
    product_lines = [f"- {item}" for item in data.get("product_blockers", [])] or ["- None"]
    warnings = sorted(set(data.get("warnings", [])))
    warning_lines = [f"- {item}" for item in warnings] or ["- None"]
    runtime = data.get("runtime", {})
    packages = runtime.get("packages", {})
    plain = data.get("conversions", {}).get("plain", {})
    mixed = data.get("conversions", {}).get("mixed", {})
    columns = data.get("conversions", {}).get("columns", {})
    fallback = data.get("fallback", {})
    lines = [
        "# D6 PDF -> Approximate DOCX Go/No-Go Probe Report",
        "",
        f"Status: **{status}**",
        "",
        "## Runtime",
        "",
        f"- Python: `{runtime.get('python', 'unknown')}` at `{runtime.get('executable', PDF_RUNTIME)}`",
        f"- PyMuPDF: `{packages.get('PyMuPDF', 'unknown')}`",
        f"- pdf2docx: `{packages.get('pdf2docx', 'unknown')}`",
        f"- python-docx: `{packages.get('python-docx', 'unknown')}`",
        f"- LibreOffice: `{SOFFICE}`",
        f"- Okapi/Tikal: `{DEFAULT_BUNDLED_TIKAL}`",
        "",
        "## Blockers",
        "",
        "### Environment",
        *env_lines,
        "",
        "### Product",
        *product_lines,
        "",
        "## Fixture Generation",
        "",
        "- `scripts/generate_pdf_fixtures.py` creates deterministic local PDFs.",
        "- Plain, double-column, scanned, corrupt, disguised, encrypted, and hybrid PDFs are generated locally.",
        "- Mixed-layout PDF is exported from `tests/fixtures/okapi_probe_mixed.docx` via LibreOffice.",
        "",
        "## Key Results",
        "",
        f"- Plain PDF inspection: `{json.dumps(data.get('pdf_inspection', {}).get('plain', {}), ensure_ascii=False)}`",
        f"- Scan rejection: `{data.get('pdf_inspection', {}).get('scan', {}).get('error_code', 'unknown')}`",
        f"- pdf2docx plain conversion: `{plain.get('conversion', {}).get('engine', 'unknown')}`, size `{plain.get('converted_docx_size', 'unknown')}` bytes",
        f"- Converted plain DOCX images/tables: `{plain.get('converted_docx_image_count', 'unknown')}` / `{plain.get('converted_docx_table_count', 'unknown')}`",
        f"- Mixed DOCX images/tables: `{mixed.get('converted_docx_image_count', 'unknown')}` / `{mixed.get('converted_docx_table_count', 'unknown')}`",
        f"- Columns DOCX images/tables: `{columns.get('converted_docx_image_count', 'unknown')}` / `{columns.get('converted_docx_table_count', 'unknown')}`",
        f"- Okapi plain blocks: `{plain.get('okapi', {}).get('blocks_count', 'unknown')}`",
        f"- Plain converted vs merged snapshot diff: `{json.dumps(plain.get('okapi', {}).get('structure_diffs', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Fallback warning: `{FALLBACK_WARNING if fallback else 'not run'}`",
        f"- Fallback Okapi blocks: `{fallback.get('okapi', {}).get('blocks_count', 'unknown')}`",
        f"- Fallback converted vs merged snapshot diff: `{json.dumps(fallback.get('okapi', {}).get('structure_diffs', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- LibreOffice rendered plain pages: `{plain.get('converted_render', {}).get('pdf_pages', 'unknown')}`",
        f"- LibreOffice rendered fallback pages: `{fallback.get('render', {}).get('pdf_pages', 'unknown')}`",
        "",
        "## Warnings",
        "",
        *warning_lines,
        "",
        "## Recommendation",
        "",
        "Only move to production `extract_document()` PDF integration if this report status is `GO`. D6 itself does not wire PDF into production.",
        "",
        "## Probe Data",
        "",
        "```json",
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--no-force-fallback", action="store_true")
    args = parser.parse_args()
    status, data = run_probe(force_fallback=not args.no_force_fallback)
    assert status in ALLOWED_STATUS
    run_report = Path(data.get("run_dir", RUNS_DIR)) / "D6_PDF_PROBE_REPORT.md"
    write_report(run_report, status, data)
    write_report(args.report, status, data)
    print(f"{status}: wrote {args.report} and {run_report}")
    return 0 if status == "GO" else 2 if status == "NO-GO" else 3


if __name__ == "__main__":
    raise SystemExit(main())
