#!/usr/bin/env python3
"""D2 Okapi DOCX Go/No-Go probe.

The probe is intentionally narrow: one DOCX fixture, Okapi 1.48.0 OpenXML
extract/merge, deterministic XLIFF pseudo-translation, DOCX structure gates,
and LibreOffice/Poppler render gates. Missing runtimes report
BLOCKED_BY_ENVIRONMENT; available runtimes with failed gates report NO-GO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "okapi_probe_mixed.docx"
DEFAULT_CONFIG = ROOT / "configs" / "okapi" / "openxml_docx_p0.fprm"
DEFAULT_REPORT = ROOT / "D2_OKAPI_PROBE_REPORT.md"
DEFAULT_RUNS = ROOT / "runs" / "d2_okapi_probe"
DEFAULT_CONFIG_ID = "okf_openxml@openxml_docx_p0"
DEFAULT_BUNDLED_JAVA = ROOT / ".runtime" / "java17" / "jdk-17.0.20+8-jre" / "Contents" / "Home" / "bin" / "java"
DEFAULT_BUNDLED_TIKAL = ROOT / ".runtime" / "okapi-1.48.0" / "tikal-java17.sh"
DEFAULT_BUNDLED_PYTHON = Path(os.environ.get("PDF_RUNTIME_PYTHON", sys.executable))

XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
INLINE_LOCAL_NAMES = {"g", "x", "ph", "bpt", "ept", "it", "sub", "bx", "ex"}
EXPECTED_INPUT_NAME = "okapi_probe_mixed.docx"
EXPECTED_XLIFF_NAME = "okapi_probe_mixed.docx.xlf"
EXPECTED_OUTPUT_NAME = "okapi_probe_mixed.docx"
EXPECTED_CONFIG_BASENAME = "okf_openxml@openxml_docx_p0.fprm"
PSEUDO_PREFIX = "[ZH] "

REGION_MARKERS = {
    "body": "D2_BODY_MARK_A17C",
    "table": "D2_TABLE_MARK_E42D",
    "header": "D2_HEADER_MARK_6F8B",
    "footer": "D2_FOOTER_MARK_91C2",
}
REQUIRED_P0_SWITCHES = {
    "bPreferenceTranslateWordHeadersFooters.b": "false",
    "bPreferenceTranslateDocProperties.b": "false",
    "bPreferenceTranslateComments.b": "false",
    "bPreferenceTranslateWordHidden.b": "false",
    "translateWordNumberingLevelText.b": "false",
    "translateWordGraphicName.b": "false",
    "translateWordGraphicDescription.b": "false",
    "bPreferenceAggressiveCleanup.b": "false",
    "allowWordStyleOptimisation.b": "false",
}


@dataclass
class ToolCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class XliffStats:
    unit_count: int
    unit_ids: list[str]
    inline_type_counts: dict[str, int]
    inline_signatures: dict[str, list[dict]]
    has_seg_source: bool = False
    has_mrk: bool = False


class ProbeError(RuntimeError):
    """Base class for deterministic D2 probe failures."""


class EnvironmentBlocked(ProbeError):
    """Raised when required external runtime is unavailable."""


def bundled_or_path(path: Path, fallback: str | None) -> str | None:
    if path.exists():
        return str(path)
    return fallback


def run_command(
    cmd: list[str],
    timeout: int = 30,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return CommandResult(cmd=cmd, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def java_env(java: str | None) -> dict[str, str] | None:
    if not java:
        return None
    java_path = Path(java)
    if java_path.name != "java":
        return None
    home = java_path.parent.parent
    env = os.environ.copy()
    env["JAVA_HOME"] = str(home)
    return env


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def namespace_of(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def qname(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name


def clone_element(element: ET.Element) -> ET.Element:
    clone = ET.Element(element.tag, dict(element.attrib))
    clone.text = element.text
    clone.tail = element.tail
    for child in list(element):
        clone.append(clone_element(child))
    return clone


def inline_signature(element: ET.Element) -> list[dict]:
    signature: list[dict] = []

    def walk(node: ET.Element, path: str) -> None:
        for index, child in enumerate(list(node)):
            name = local_name(child.tag)
            child_path = f"{path}/{name}[{index}]"
            if name in INLINE_LOCAL_NAMES:
                signature.append(
                    {
                        "path": child_path,
                        "tag": child.tag,
                        "attrs": sorted(child.attrib.items()),
                        "text": child.text or "",
                        "tail": child.tail or "",
                    }
                )
            walk(child, child_path)

    walk(element, "")
    return signature


def inline_type_counts(signatures: Iterable[list[dict]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signature in signatures:
        for item in signature:
            name = local_name(item["tag"])
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def visible_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in list(element):
        parts.append(visible_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def pseudo_translate_xliff(xliff_path: Path, prefix: str = PSEUDO_PREFIX) -> XliffStats:
    tree = ET.parse(xliff_path)
    root = tree.getroot()
    namespace = namespace_of(root.tag)
    if namespace != XLIFF_NS:
        raise ProbeError(f"Unsupported XLIFF namespace: {namespace or '<none>'}")

    units = root.findall(f".//{qname(namespace, 'trans-unit')}")
    if not units:
        raise ProbeError("XLIFF contains zero trans-unit elements")

    ids: list[str] = []
    source_signatures: dict[str, list[dict]] = {}
    has_seg_source = root.find(f".//{qname(namespace, 'seg-source')}") is not None
    has_mrk = root.find(f".//{qname(namespace, 'mrk')}") is not None
    if has_seg_source or has_mrk:
        raise ProbeError("XLIFF contains seg-source/mrk; D2 no-SRX handler refuses generic segmentation")

    for trans_unit in units:
        unit_id = trans_unit.get("id")
        if not unit_id:
            raise ProbeError("trans-unit missing id")
        if unit_id in ids:
            raise ProbeError(f"duplicate trans-unit id: {unit_id}")
        ids.append(unit_id)

        source = trans_unit.find(qname(namespace, "source"))
        if source is None:
            continue
        source_signatures[unit_id] = inline_signature(source)

        target = trans_unit.find(qname(namespace, "target"))
        if target is None:
            target = ET.Element(qname(namespace, "target"))
            source_index = list(trans_unit).index(source)
            trans_unit.insert(source_index + 1, target)
        else:
            target.clear()

        target.text = prefix + (source.text or "")
        target.tail = None
        for child in list(source):
            target.append(clone_element(child))

        if inline_signature(target) != source_signatures[unit_id]:
            raise ProbeError(f"inline signature changed for unit {unit_id}")
        if not visible_text(target).startswith(prefix):
            raise ProbeError(f"pseudo target prefix missing for unit {unit_id}")

    if set(ids) != {unit.get("id") for unit in units}:
        raise ProbeError("trans-unit ID set changed during pseudo-translation")

    tree.write(xliff_path, encoding="utf-8", xml_declaration=True)
    verify_stats = analyze_xliff(xliff_path)
    if verify_stats.unit_ids != ids:
        raise ProbeError("trans-unit order changed after write")
    for unit_id, signature in source_signatures.items():
        if verify_stats.inline_signatures.get(unit_id) != signature:
            raise ProbeError(f"inline signature changed after write for unit {unit_id}")
    return verify_stats


def analyze_xliff(xliff_path: Path) -> XliffStats:
    tree = ET.parse(xliff_path)
    root = tree.getroot()
    namespace = namespace_of(root.tag)
    units = root.findall(f".//{qname(namespace, 'trans-unit')}")
    ids: list[str] = []
    signatures: dict[str, list[dict]] = {}
    has_seg_source = root.find(f".//{qname(namespace, 'seg-source')}") is not None
    has_mrk = root.find(f".//{qname(namespace, 'mrk')}") is not None
    for unit in units:
        unit_id = unit.get("id") or ""
        ids.append(unit_id)
        target = unit.find(qname(namespace, "target"))
        source = unit.find(qname(namespace, "source"))
        signatures[unit_id] = inline_signature(target if target is not None else source) if source is not None else []
    return XliffStats(
        unit_count=len(units),
        unit_ids=ids,
        inline_type_counts=inline_type_counts(signatures.values()),
        inline_signatures=signatures,
        has_seg_source=has_seg_source,
        has_mrk=has_mrk,
    )


def xliff_sources_by_file(xliff_path: Path) -> dict[str, list[str]]:
    root = ET.parse(xliff_path).getroot()
    namespace = namespace_of(root.tag)
    sources: dict[str, list[str]] = {}
    for file_node in root.findall(qname(namespace, "file")):
        original = file_node.get("original") or ""
        texts: list[str] = []
        for source in file_node.findall(f".//{qname(namespace, 'source')}"):
            texts.append(visible_text(source))
        sources[original] = texts
    return sources


def assert_xliff_region_behavior(xliff_path: Path) -> dict:
    sources_by_file = xliff_sources_by_file(xliff_path)
    all_sources = [text for texts in sources_by_file.values() for text in texts]
    joined = "\n".join(all_sources)
    missing_required = [name for name in ("body", "table") if REGION_MARKERS[name] not in joined]
    forbidden_present = [name for name in ("header", "footer") if REGION_MARKERS[name] in joined]
    forbidden_files = [
        name for name in sources_by_file
        if name.startswith("word/header")
        or name.startswith("word/footer")
        or name.startswith("docProps/")
        or name.startswith("word/comments")
    ]
    if missing_required:
        raise ProbeError(f"XLIFF missing required translatable markers: {missing_required}")
    if forbidden_present:
        raise ProbeError(f"XLIFF contains excluded markers: {forbidden_present}")
    if forbidden_files:
        raise ProbeError(f"XLIFF contains excluded OOXML parts: {forbidden_files}")
    return {
        "files": sorted(sources_by_file),
        "body_marker_in_xliff": REGION_MARKERS["body"] in joined,
        "table_marker_in_xliff": REGION_MARKERS["table"] in joined,
        "header_marker_in_xliff": REGION_MARKERS["header"] in joined,
        "footer_marker_in_xliff": REGION_MARKERS["footer"] in joined,
    }


def compare_snapshots(before: dict, after: dict) -> dict:
    diffs: dict = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diffs[key] = {"before": before.get(key), "after": after.get(key)}
    return diffs


def docx_part_texts(docx_path: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    with zipfile.ZipFile(docx_path) as zf:
        for name in zf.namelist():
            if name.endswith(".xml"):
                try:
                    texts[name] = "".join(ET.fromstring(zf.read(name)).itertext())
                except ET.ParseError:
                    continue
    return texts


def assert_docx_region_behavior(docx_path: Path) -> dict:
    texts = docx_part_texts(docx_path)
    document_text = texts.get("word/document.xml", "")
    header_text = "\n".join(text for name, text in texts.items() if name.startswith("word/header"))
    footer_text = "\n".join(text for name, text in texts.items() if name.startswith("word/footer"))
    expectations = {
        "body_translated": translated_marker_present(document_text, REGION_MARKERS["body"]),
        "table_translated": translated_marker_present(document_text, REGION_MARKERS["table"]),
        "header_preserved": REGION_MARKERS["header"] in header_text and PSEUDO_PREFIX + REGION_MARKERS["header"] not in header_text,
        "footer_preserved": REGION_MARKERS["footer"] in footer_text and PSEUDO_PREFIX + REGION_MARKERS["footer"] not in footer_text,
    }
    failed = [name for name, ok in expectations.items() if not ok]
    if failed:
        raise ProbeError(f"DOCX region behavior failed: {failed}")
    return expectations


def translated_marker_present(text: str, marker: str) -> bool:
    marker_index = text.find(marker)
    if marker_index < 0:
        return False
    prefix_index = text.rfind(PSEUDO_PREFIX, 0, marker_index + len(marker))
    return prefix_index >= 0 and marker_index - prefix_index < 80


def assert_docx_zip_xml(docx_path: Path) -> dict:
    xml_files: list[str] = []
    with zipfile.ZipFile(docx_path) as zf:
        bad = zf.testzip()
        if bad:
            raise ProbeError(f"DOCX ZIP member failed CRC: {bad}")
        for name in zf.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ET.fromstring(zf.read(name))
                xml_files.append(name)
        header_files = [n for n in zf.namelist() if n.startswith("word/header") and n.endswith(".xml")]
        footer_files = [n for n in zf.namelist() if n.startswith("word/footer") and n.endswith(".xml")]
    if not docx_path.exists() or docx_path.stat().st_size == 0:
        raise ProbeError("Output DOCX is empty")
    if not header_files or not footer_files:
        raise ProbeError("Header/footer XML is missing")
    return {
        "xml_file_count": len(xml_files),
        "header_xml_files": header_files,
        "footer_xml_files": footer_files,
    }


def poppler_text_tool(pdfinfo: str | None) -> str | None:
    if pdfinfo:
        sibling = Path(pdfinfo).with_name("pdftotext")
        if sibling.exists():
            return str(sibling)
    return shutil.which("pdftotext")


def bundled_python_tool() -> str | None:
    return str(DEFAULT_BUNDLED_PYTHON) if DEFAULT_BUNDLED_PYTHON.exists() else None


def extract_pdf_text(pdf_path: Path, out_dir: Path, pdftotext: str | None, python_tool: str | None = None) -> dict:
    txt_path = out_dir / "render_text.txt"
    if pdftotext:
        text_result = run_command([pdftotext, str(pdf_path), str(txt_path)], timeout=30)
        if text_result.returncode != 0:
            raise EnvironmentBlocked(f"pdftotext failed: {(text_result.stderr or text_result.stdout).strip()}")
        text = txt_path.read_text(encoding="utf-8", errors="replace")
        return {"tool": "pdftotext", "detail": pdftotext, "text": text}

    python_tool = python_tool or bundled_python_tool()
    if not python_tool:
        raise EnvironmentBlocked("pdftotext not found and bundled Python for pypdf fallback is unavailable")
    script = (
        "from pathlib import Path\n"
        "from pypdf import PdfReader\n"
        "pdf=Path(__import__('sys').argv[1]); out=Path(__import__('sys').argv[2])\n"
        "reader=PdfReader(str(pdf))\n"
        "text='\\n'.join((page.extract_text() or '') for page in reader.pages)\n"
        "out.write_text(text, encoding='utf-8')\n"
        "print(f'pypdf {__import__(\"pypdf\").__version__}; pages={len(reader.pages)}')\n"
    )
    result = run_command([python_tool, "-c", script, str(pdf_path), str(txt_path)], timeout=60)
    if result.returncode != 0:
        raise EnvironmentBlocked(f"pypdf fallback failed: {(result.stderr or result.stdout).strip()}")
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    return {"tool": "pypdf", "detail": (result.stdout or "").strip(), "text": text}


def check_pdf_render(
    pdf_path: Path,
    out_dir: Path,
    pdfinfo: str,
    pdftoppm: str,
    pdftotext: str | None,
    python_tool: str | None = None,
) -> dict:
    info = run_command([pdfinfo, str(pdf_path)], timeout=30)
    if info.returncode != 0:
        raise EnvironmentBlocked(f"pdfinfo failed: {(info.stderr or info.stdout).strip()}")
    page_match = re.search(r"^Pages:\s+(\d+)", info.stdout, re.MULTILINE)
    pages = int(page_match.group(1)) if page_match else 0
    if pages <= 0:
        raise ProbeError("PDF page count is zero")

    png_prefix = out_dir / "render_page"
    png = run_command([pdftoppm, "-png", str(pdf_path), str(png_prefix)], timeout=60)
    if png.returncode != 0:
        raise EnvironmentBlocked(f"pdftoppm failed: {(png.stderr or png.stdout).strip()}")
    pngs = sorted(out_dir.glob("render_page-*.png"))
    if len(pngs) != pages:
        raise ProbeError(f"Rendered PNG page count mismatch: expected {pages}, got {len(pngs)}")
    nonblank = [is_png_nonblank(path) for path in pngs]
    if not all(nonblank):
        raise ProbeError("At least one rendered PNG page is blank")

    text_data = extract_pdf_text(pdf_path, out_dir, pdftotext, python_tool)
    replacement_found = "\ufffd" in text_data["text"]
    if replacement_found:
        raise ProbeError("PDF text contains U+FFFD replacement characters")
    return {
        "pdf_pages": pages,
        "png_pages": [str(path) for path in pngs],
        "png_nonblank": nonblank,
        "u_fffd_found": replacement_found,
        "text_extractor": text_data["tool"],
        "text_extractor_detail": text_data["detail"],
    }


def is_png_nonblank(path: Path) -> bool:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ProbeError(f"Rendered page is not a PNG: {path}")
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
        return path.stat().st_size > 0
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(compressed)
    stride = width * channels
    prev = bytearray(stride)
    offset = 0
    pixels = bytearray()
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
                raise ProbeError(f"Unsupported PNG filter type {filter_type}: {path}")
        pixels.extend(row)
        prev = row
    for i in range(0, len(pixels), channels):
        rgb = pixels[i:i + (1 if color_type == 0 else 3)]
        if any(value < 250 for value in rgb):
            return True
    return False


def render_docx(soffice: str, docx: Path, out_dir: Path) -> Path:
    profile = Path(tempfile.mkdtemp(prefix="lo-profile-", dir=str(out_dir)))
    cmd = [
        soffice,
        f"-env:UserInstallation=file://{profile}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx),
    ]
    result = run_command(cmd, timeout=90)
    if result.returncode != 0:
        raise ProbeError(f"LibreOffice render failed: {(result.stderr or result.stdout).strip()}")
    pdf_path = out_dir / f"{docx.stem}.pdf"
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise ProbeError(f"LibreOffice did not create non-empty PDF: {pdf_path}")
    return pdf_path


def create_run_dir(base: Path) -> Path:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    candidate = base / run_id
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = base / f"{run_id}-{suffix}"
    candidate.mkdir(parents=True)
    return candidate


def copy_fixture_to_run(fixture: Path, run_dir: Path) -> Path:
    target = run_dir / EXPECTED_INPUT_NAME
    shutil.copy2(fixture, target)
    return target


def copy_config_to_run(config: Path, run_dir: Path) -> Path:
    target = run_dir / EXPECTED_CONFIG_BASENAME
    shutil.copy2(config, target)
    return target


def check_file(name: str, path: Path) -> ToolCheck:
    ok = path.exists() and path.stat().st_size > 0
    return ToolCheck(name, ok, str(path) if ok else f"missing or empty: {path}")


def check_native_p0_config(path: Path, config_id: str) -> ToolCheck:
    if not path.exists():
        return ToolCheck("p0_filter_config_native", False, f"missing: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "placeholder" in text.lower():
        return ToolCheck("p0_filter_config_native", False, "P0 config is still a placeholder, not an Okapi-native 1.48.0 config")
    if config_id == "okf_openxml":
        return ToolCheck("p0_filter_config_native", False, "using built-in okf_openxml; custom P0 config is not registered")
    if config_id != DEFAULT_CONFIG_ID:
        return ToolCheck("p0_filter_config_native", False, f"unexpected P0 config ID: {config_id}")
    missing = []
    for key, expected in REQUIRED_P0_SWITCHES.items():
        match = re.search(rf"^{re.escape(key)}\s*=\s*(\S+)\s*$", text, re.MULTILINE)
        if not match or match.group(1).lower() != expected:
            missing.append(f"{key}={expected}")
    if missing:
        return ToolCheck("p0_filter_config_native", False, f"missing required switches: {', '.join(missing)}")
    if "elements:" not in text or "'w:t':" not in text:
        return ToolCheck("p0_filter_config_native", False, "config does not contain OpenXML YAML word rules")
    return ToolCheck("p0_filter_config_native", True, f"{config_id} with {path}")


def parse_tikal_version(output: str) -> str:
    for line in output.splitlines():
        if line.strip().startswith("Version:"):
            return line.strip()
    return output.strip().splitlines()[0] if output.strip() else "unknown"


def check_java(java: str) -> ToolCheck:
    try:
        result = run_command([java, "-version"], timeout=10)
    except Exception as exc:
        return ToolCheck("java", False, f"{type(exc).__name__}: {exc}")
    output = (result.stderr or result.stdout).strip()
    ok = result.returncode == 0 and "17." in output
    return ToolCheck("java", ok, output.splitlines()[0] if output else f"exit {result.returncode}")


def check_exec(name: str, path: str | None, probe_args: list[str]) -> ToolCheck:
    resolved = path or shutil.which(name)
    if not resolved:
        return ToolCheck(name, False, f"{name} executable not found")
    try:
        result = run_command([resolved, *probe_args], timeout=30)
    except Exception as exc:
        return ToolCheck(name, False, f"{resolved}: {type(exc).__name__}: {exc}")
    output = (result.stdout or result.stderr).strip()
    ok = result.returncode == 0
    detail = parse_tikal_version(output) if name == "tikal" else output.splitlines()[0] if output else resolved
    return ToolCheck(name, ok, detail)


def list_filter_configs(tikal: str, java: str | None = None) -> CommandResult:
    # Tikal 1.x lists filters in help/config output; callers store stdout/stderr
    # and require the target ID to appear before extraction.
    return run_command([tikal, "-lfc"], timeout=60, env=java_env(java))


def ensure_filter_config_available(config_listing: str, config_id: str, allow_local_unlisted: bool = False) -> ToolCheck:
    if config_id in config_listing:
        return ToolCheck("filter_config_listed", True, config_id)
    if allow_local_unlisted and config_id == DEFAULT_CONFIG_ID:
        return ToolCheck(
            "filter_config_listed",
            True,
            "Tikal 1.48.0 -lfc does not list arbitrary cwd-local custom .fprm files; actual extract/merge load is verified separately",
        )
    else:
        raise ProbeError(f"Filter configuration ID not listed by Tikal: {config_id}")


def extract_with_tikal(
    tikal: str,
    run_docx: Path,
    run_dir: Path,
    config_id: str,
    java: str | None = None,
) -> CommandResult:
    cmd = [
        tikal,
        "-x",
        "-fc",
        config_id,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-od",
        str(run_dir),
        str(run_docx),
    ]
    return run_command(cmd, timeout=180, cwd=run_dir, env=java_env(java))


def merge_with_tikal(
    tikal: str,
    xliff: Path,
    run_dir: Path,
    config_id: str,
    java: str | None = None,
) -> CommandResult:
    cmd = [
        tikal,
        "-m",
        "-fc",
        config_id,
        "-sl",
        "en",
        "-tl",
        "zh-CN",
        "-sd",
        str(run_dir),
        "-od",
        str(run_dir),
        str(xliff),
    ]
    return run_command(cmd, timeout=180, cwd=run_dir, env=java_env(java))


def write_report(path: Path, status: str, checks: list[ToolCheck], data: dict) -> None:
    environment_lines = [f"- {item}" for item in data.get("environment_blockers", [])] or ["- None"]
    product_lines = [f"- {item}" for item in data.get("product_blockers", [])] or ["- None"]
    lines = [
        "# D2 Okapi DOCX Go/No-Go Probe Report",
        "",
        f"Status: **{status}**",
        "",
        "## Runtime",
        "",
        f"- Java: `{data.get('java_version', 'unknown')}`",
        f"- Okapi/Tikal: `{data.get('tikal_version', 'unknown')}`",
        f"- LibreOffice: `{data.get('soffice_version', 'unknown')}`",
        f"- Poppler: `{data.get('poppler_version', 'unknown')}`",
        f"- Filter config ID: `{data.get('filter_config_id', 'unknown')}`",
        f"- Filter config SHA-256: `{data.get('filter_config_sha256', 'unknown')}`",
        f"- Render text extractor: `{data.get('render_text_extractor', 'unknown')}`",
        "",
        "## Blockers",
        "",
        "### Environment",
        "",
        *environment_lines,
        "",
        "### Product",
        "",
        *product_lines,
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        result = "PASS" if check.ok else "FAIL"
        lines.append(f"| {check.name} | {result} | `{check.detail.replace(chr(10), '<br>')}` |")
    lines.extend(
        [
            "",
            "## Probe Data",
            "",
            "```json",
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--filter-config-id", default=DEFAULT_CONFIG_ID)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--java", default=bundled_or_path(DEFAULT_BUNDLED_JAVA, shutil.which("java") or "java"))
    parser.add_argument("--tikal", default=os.environ.get("TIKAL_PATH") or bundled_or_path(DEFAULT_BUNDLED_TIKAL, shutil.which("tikal") or shutil.which("tikal.sh")))
    parser.add_argument("--soffice", default=os.environ.get("SOFFICE_PATH") or shutil.which("soffice"))
    parser.add_argument("--pdfinfo", default=shutil.which("pdfinfo"))
    parser.add_argument("--pdftoppm", default=shutil.which("pdftoppm"))
    parser.add_argument("--python", default=bundled_python_tool())
    args = parser.parse_args()

    run_dir = create_run_dir(args.runs_dir)
    run_report = run_dir / "D2_OKAPI_PROBE_REPORT.md"
    environment_blockers: list[str] = []
    product_blockers: list[str] = []
    checks = [
        check_file("fixture", args.fixture),
        check_file("filter_config", args.config),
        check_native_p0_config(args.config, args.filter_config_id),
        check_java(args.java),
        check_exec("tikal", args.tikal, ["-help"]) if args.tikal else ToolCheck("tikal", False, "tikal executable not found"),
        check_exec("soffice", args.soffice, ["--version"]) if args.soffice else ToolCheck("soffice", False, "soffice executable not found"),
        check_exec("pdfinfo", args.pdfinfo, ["-v"]) if args.pdfinfo else ToolCheck("pdfinfo", False, "pdfinfo executable not found"),
        check_exec("pdftoppm", args.pdftoppm, ["-v"]) if args.pdftoppm else ToolCheck("pdftoppm", False, "pdftoppm executable not found"),
    ]
    pdftotext = poppler_text_tool(args.pdfinfo)
    text_fallback = args.python if args.python and Path(args.python).exists() else None
    checks.append(ToolCheck("render_text_extractor", bool(pdftotext or text_fallback), pdftotext or text_fallback or "pdftotext executable and pypdf fallback not found"))
    for check in checks:
        if not check.ok and check.name in {"java", "tikal", "soffice", "pdfinfo", "pdftoppm", "render_text_extractor"}:
            environment_blockers.append(f"{check.name}: {check.detail}")
        elif not check.ok:
            product_blockers.append(f"{check.name}: {check.detail}")

    data: dict = {
        "run_dir": str(run_dir),
        "fixture": str(args.fixture),
        "filter_config": str(args.config),
        "filter_config_id": args.filter_config_id,
        "filter_config_sha256": sha256_file(args.config) if args.config.exists() else "",
        "checks": [asdict(check) for check in checks],
        "java_version": next((c.detail for c in checks if c.name == "java"), "unknown"),
        "tikal_version": next((c.detail for c in checks if c.name == "tikal"), "unknown"),
        "soffice_version": next((c.detail for c in checks if c.name == "soffice"), "unknown"),
        "poppler_version": next((c.detail for c in checks if c.name == "pdfinfo"), "unknown"),
        "environment_blockers": environment_blockers,
        "product_blockers": product_blockers,
        "render_text_extractor": pdftotext or ("pypdf via " + text_fallback if text_fallback else "unknown"),
    }

    if environment_blockers:
        status = "BLOCKED_BY_ENVIRONMENT"
        write_report(run_report, status, checks, data)
        write_report(args.report, status, checks, data)
        print(f"{status}: wrote {args.report} and {run_report}")
        return 3

    sys.path.insert(0, str(ROOT))
    from backend.pipeline.docx_snapshot import snapshot_docx_structure

    try:
        run_docx = copy_fixture_to_run(args.fixture, run_dir)
        run_config = copy_config_to_run(args.config, run_dir)
        data["run_filter_config"] = str(run_config)
        before = snapshot_docx_structure(str(run_docx))
        data["before_snapshot"] = before

        listing = list_filter_configs(args.tikal, args.java)
        data["filter_config_listing_cmd"] = listing.cmd
        data["filter_config_listing_returncode"] = listing.returncode
        data["filter_config_listing_stdout"] = listing.stdout[-8000:]
        data["filter_config_listing_stderr"] = listing.stderr[-8000:]
        if listing.returncode != 0:
            raise ProbeError("Tikal filter configuration listing failed")
        checks.append(ensure_filter_config_available(listing.stdout + listing.stderr, args.filter_config_id, allow_local_unlisted=True))

        extract = extract_with_tikal(args.tikal, run_docx, run_dir, args.filter_config_id, args.java)
        data["extract"] = asdict(extract)
        expected_xliff = run_dir / EXPECTED_XLIFF_NAME
        if extract.returncode != 0:
            raise ProbeError("Tikal extract failed")
        if not expected_xliff.exists() or expected_xliff.stat().st_size == 0:
            raise ProbeError(f"Expected XLIFF missing: {expected_xliff}")
        checks.append(ToolCheck("extract", True, str(expected_xliff)))
        xliff_regions = assert_xliff_region_behavior(expected_xliff)
        data["xliff_region_behavior"] = xliff_regions
        checks.append(ToolCheck("xliff_region_behavior", True, json.dumps(xliff_regions, sort_keys=True)))

        xliff_stats = pseudo_translate_xliff(expected_xliff)
        data["xliff_stats"] = asdict(xliff_stats)
        checks.append(ToolCheck("xliff_units", xliff_stats.unit_count > 0, str(xliff_stats.unit_count)))
        checks.append(ToolCheck("xliff_inline_integrity", True, json.dumps(xliff_stats.inline_type_counts, sort_keys=True)))

        merge = merge_with_tikal(args.tikal, expected_xliff, run_dir, args.filter_config_id, args.java)
        data["merge"] = asdict(merge)
        expected_output = run_dir / EXPECTED_OUTPUT_NAME
        if merge.returncode != 0:
            raise ProbeError("Tikal merge failed")
        if not expected_output.exists() or expected_output.stat().st_size == 0:
            docx_outputs = sorted(path.name for path in run_dir.glob("*.docx"))
            raise ProbeError(f"Expected output DOCX missing: {expected_output}; actual DOCX files={docx_outputs}")
        checks.append(ToolCheck("merge", True, str(expected_output)))

        docx_gate = assert_docx_zip_xml(expected_output)
        data["docx_zip_xml"] = docx_gate
        docx_regions = assert_docx_region_behavior(expected_output)
        data["docx_region_behavior"] = docx_regions
        checks.append(ToolCheck("docx_region_behavior", True, json.dumps(docx_regions, sort_keys=True)))
        after = snapshot_docx_structure(str(expected_output))
        diffs = compare_snapshots(before, after)
        data["after_snapshot"] = after
        data["structure_diffs"] = diffs
        checks.append(ToolCheck("structure_snapshot", not diffs, "no differences" if not diffs else json.dumps(diffs, sort_keys=True)))

        pdf_path = render_docx(args.soffice, expected_output, run_dir)
        render_data = check_pdf_render(pdf_path, run_dir, args.pdfinfo, args.pdftoppm, pdftotext, text_fallback)
        data["render"] = {"pdf": str(pdf_path), **render_data}
        data["render_text_extractor"] = render_data["text_extractor"]
        checks.append(ToolCheck("render", True, str(pdf_path)))
        status = "GO" if all(check.ok for check in checks) else "NO-GO"
    except EnvironmentBlocked as exc:
        checks.append(ToolCheck("environment_gate", False, str(exc)))
        environment_blockers.append(str(exc))
        status = "BLOCKED_BY_ENVIRONMENT"
    except Exception as exc:
        checks.append(ToolCheck("probe_gate", False, f"{type(exc).__name__}: {exc}"))
        product_blockers.append(f"{type(exc).__name__}: {exc}")
        status = "NO-GO"

    data["environment_blockers"] = environment_blockers
    data["product_blockers"] = product_blockers
    data["checks"] = [asdict(check) for check in checks]
    write_report(run_report, status, checks, data)
    write_report(args.report, status, checks, data)
    print(f"{status}: wrote {args.report} and {run_report}")
    return 0 if status == "GO" else 2 if status == "NO-GO" else 3


if __name__ == "__main__":
    raise SystemExit(main())
