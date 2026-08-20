"""OOXML CJK font normalization for merged DOCX outputs."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from copy import deepcopy
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"
FONT_TABLE_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable"
FONT_TABLE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
WORD_XML_PART_RE = re.compile(r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml$")
TEXT_BOX_PART_RE = re.compile(r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml$")
HEADING_STYLE_RE = re.compile(r"(heading|title|subtitle|caption)", re.IGNORECASE)
W = f"{{{WORD_NS}}}"
R = f"{{{DOC_REL_NS}}}"
PKG_REL = f"{{{REL_NS}}}"
CT = f"{{{CONTENT_TYPES_NS}}}"


class CjkFontError(ValueError):
    """Stable DOCUMENT_* CJK font error."""


@dataclass(frozen=True)
class CjkFontChoice:
    serif: str
    sans: str
    source: str


def cjk_font_error(code: str, detail: str) -> CjkFontError:
    return CjkFontError(f"{code}: {detail}")


def apply_cjk_fonts(docx_path: Path, force_render_font: bool = True) -> dict:
    """Set every run's ascii/hAnsi/cs/eastAsia to a single CJK font (SimHei).

    ``force_render_font`` is kept for call-site compatibility but is always treated
    as True: every run (including Latin text) is unified to SimHei.
    """
    docx_path = docx_path.resolve()
    if not zipfile.is_zipfile(docx_path):
        raise cjk_font_error("DOCUMENT_INTEGRITY_ERROR", "merged output is not a valid DOCX ZIP")
    if not docx_contains_cjk(docx_path):
        return {"applied": False, "reason": "no CJK text"}

    choice = choose_cjk_fonts()
    with zipfile.ZipFile(docx_path) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
        infos = {info.filename: info for info in zf.infolist()}

    changed_parts: list[str] = []
    for name in sorted(entries):
        if not WORD_XML_PART_RE.match(name):
            continue
        updated = _apply_fonts_to_xml_part(entries[name], choice, True)
        if updated != entries[name]:
            entries[name] = updated
            changed_parts.append(name)

    if changed_parts:
        entries["word/fontTable.xml"] = _updated_font_table(entries.get("word/fontTable.xml"), choice)
        entries["[Content_Types].xml"] = _updated_content_types(entries.get("[Content_Types].xml"))
        entries["word/_rels/document.xml.rels"] = _updated_document_rels(entries.get("word/_rels/document.xml.rels"))
        _rewrite_docx(docx_path, entries, infos)

    return {
        "applied": bool(changed_parts),
        "serif": choice.serif,
        "sans": choice.sans,
        "source": choice.source,
        "force_render_font": True,
        "changed_parts": changed_parts,
    }


def docx_contains_cjk(docx_path: Path) -> bool:
    with zipfile.ZipFile(docx_path) as zf:
        for name in zf.namelist():
            if name.endswith(".xml") and name.startswith("word/"):
                try:
                    text = ET.fromstring(zf.read(name)).itertext()
                except ET.ParseError:
                    continue
                if CJK_RE.search("".join(text)):
                    return True
    return False


def choose_cjk_fonts() -> CjkFontChoice:
    return CjkFontChoice(serif="黑体", sans="黑体", source="fixed")


def font_available(name: str) -> bool:
    fc_match = shutil.which("fc-match")
    if fc_match:
        try:
            import subprocess

            result = subprocess.run([fc_match, "-f", "%{family}\n", name], text=True, capture_output=True, timeout=10, check=False)
        except (subprocess.TimeoutExpired, OSError):
            result = subprocess.CompletedProcess([], 1, "", "")
        if result.returncode == 0:
            families = {piece.strip().lower() for line in (result.stdout or "").splitlines() for piece in line.split(",")}
            if name.lower() in families:
                return True
    if _atsutil_font_available(name):
        return True
    compact = re.sub(r"\s+", "", name).lower()
    for root in _font_roots():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and compact in re.sub(r"\s+", "", path.stem).lower():
                return True
    return False


def _atsutil_font_available(name: str) -> bool:
    atsutil = shutil.which("atsutil")
    if not atsutil:
        return False
    try:
        import subprocess

        result = subprocess.run([atsutil, "fonts", "-list"], text=True, capture_output=True, timeout=15, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    wanted = name.strip().lower()
    return any(line.strip().lower() == wanted for line in (result.stdout or "").splitlines())


def _font_roots() -> list[Path]:
    return [
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]


def _apply_fonts_to_xml_part(raw: bytes, choice: CjkFontChoice, force_render_font: bool) -> bytes:
    root = ET.fromstring(raw)
    changed = False
    for paragraph in root.iter(f"{W}p"):
        cjk_font = _font_for_paragraph(paragraph, choice)
        if _apply_fonts_inside(paragraph, cjk_font, force_render_font):
            changed = True
        ppr = paragraph.find(f"{W}pPr")
        if ppr is not None:
            mark_rpr = ppr.find(f"{W}rPr")
            if mark_rpr is not None and _set_cjk_rpr(mark_rpr, cjk_font, True):
                changed = True
    return _serialize_openxml(root, raw) if changed else raw


def _apply_fonts_inside(parent: ET.Element, cjk_font: str, force_render_font: bool) -> bool:
    changed = False
    index = 0
    while index < len(parent):
        child = parent[index]
        if child.tag == f"{W}r":
            if _run_needs_mixed_split(child, force_render_font):
                replacement = _split_mixed_run(child, cjk_font)
                parent.remove(child)
                for offset, new_run in enumerate(replacement):
                    parent.insert(index + offset, new_run)
                index += len(replacement)
                changed = True
                continue
            if _apply_fonts_to_run(child, cjk_font, force_render_font):
                changed = True
        else:
            if _apply_fonts_inside(child, cjk_font, force_render_font):
                changed = True
        index += 1
    return changed


def _apply_fonts_to_run(run: ET.Element, cjk_font: str, force_render_font: bool) -> bool:
    run_text = "".join(node.text or "" for node in run.findall(f".//{W}t"))
    if not run_text.strip():
        return False
    rpr = _first_or_create(run, f"{W}rPr", 0)
    return _set_cjk_rpr(rpr, cjk_font, True)


def _run_needs_mixed_split(run: ET.Element, force_render_font: bool) -> bool:
    if not force_render_font or not _is_plain_text_run(run):
        return False
    text = "".join(child.text or "" for child in run if child.tag == f"{W}t")
    return bool(CJK_RE.search(text) and not _is_pure_cjk_text(text))


def _is_plain_text_run(run: ET.Element) -> bool:
    for child in run:
        if child.tag not in {f"{W}rPr", f"{W}t"}:
            return False
    return True


def _is_pure_cjk_text(text: str) -> bool:
    significant = [char for char in text if not char.isspace()]
    return bool(significant) and all(CJK_RE.fullmatch(char) for char in significant)


def _split_mixed_run(run: ET.Element, cjk_font: str) -> list[ET.Element]:
    rpr = run.find(f"{W}rPr")
    pieces: list[tuple[str, bool, dict[str, str]]] = []
    for text_node in [child for child in run if child.tag == f"{W}t"]:
        text = text_node.text or ""
        if not text:
            pieces.append(("", False, dict(text_node.attrib)))
            continue
        start = 0
        current_is_cjk = bool(CJK_RE.fullmatch(text[0]))
        for offset, char in enumerate(text[1:], start=1):
            char_is_cjk = bool(CJK_RE.fullmatch(char))
            if char_is_cjk != current_is_cjk:
                pieces.append((text[start:offset], current_is_cjk, dict(text_node.attrib)))
                start = offset
                current_is_cjk = char_is_cjk
        pieces.append((text[start:], current_is_cjk, dict(text_node.attrib)))

    new_runs: list[ET.Element] = []
    for text, _, attrib in pieces:
        new_run = ET.Element(f"{W}r")
        if rpr is not None:
            new_run.append(deepcopy(rpr))
        else:
            new_run.append(ET.Element(f"{W}rPr"))
        new_rpr = new_run.find(f"{W}rPr")
        if new_rpr is not None:
            _set_cjk_rpr(new_rpr, cjk_font, True)
        text_element = ET.Element(f"{W}t", attrib)
        text_element.text = text
        if _needs_preserve_space(text):
            text_element.set(f"{{{XML_NS}}}space", "preserve")
        new_run.append(text_element)
        new_runs.append(new_run)
    return new_runs


def _needs_preserve_space(text: str) -> bool:
    return bool(text) and (text[0].isspace() or text[-1].isspace())


def _set_cjk_rpr(rpr: ET.Element, cjk_font: str, force_font_slots: bool) -> bool:
    changed = False
    rfonts = rpr.find(f"{W}rFonts")
    if rfonts is None:
        rfonts = ET.Element(f"{W}rFonts")
        rpr.insert(0, rfonts)
    if rfonts.get(f"{W}eastAsia") != cjk_font:
        rfonts.set(f"{W}eastAsia", cjk_font)
        changed = True
    if rfonts.get(f"{W}hint") != "eastAsia":
        rfonts.set(f"{W}hint", "eastAsia")
        changed = True
    if force_font_slots:
        for attr in ("ascii", "hAnsi", "cs"):
            qattr = f"{W}{attr}"
            if rfonts.get(qattr) != cjk_font:
                rfonts.set(qattr, cjk_font)
                changed = True
    lang = rpr.find(f"{W}lang")
    if lang is None:
        lang = ET.Element(f"{W}lang")
        rpr.append(lang)
    if lang.get(f"{W}eastAsia") != "zh-CN":
        lang.set(f"{W}eastAsia", "zh-CN")
        changed = True
    return changed


def _font_for_paragraph(paragraph: ET.Element, choice: CjkFontChoice) -> str:
    pstyle = paragraph.find(f"./{W}pPr/{W}pStyle")
    style_value = pstyle.get(f"{W}val", "") if pstyle is not None else ""
    if HEADING_STYLE_RE.search(style_value):
        return choice.sans
    return choice.serif


def _first_or_create(parent: ET.Element, tag: str, index: int) -> ET.Element:
    existing = parent.find(tag)
    if existing is not None:
        return existing
    node = ET.Element(tag)
    parent.insert(index, node)
    return node


def _updated_font_table(raw: bytes | None, choice: CjkFontChoice) -> bytes:
    if raw:
        root = ET.fromstring(raw)
    else:
        root = ET.Element(f"{W}fonts")
    existing = {font.get(f"{W}name") for font in root.findall(f"{W}font")}
    for name in [choice.serif, choice.sans]:
        if name not in existing:
            font = ET.Element(f"{W}font", {f"{W}name": name})
            ET.SubElement(font, f"{W}charset", {f"{W}val": "86"})
            ET.SubElement(font, f"{W}family", {f"{W}val": "auto"})
            root.append(font)
            existing.add(name)
    return _serialize_openxml(root, raw)


def _updated_content_types(raw: bytes | None) -> bytes:
    if raw:
        root = ET.fromstring(raw)
    else:
        root = ET.Element(f"{CT}Types")
    for override in root.findall(f"{CT}Override"):
        if override.get("PartName") == "/word/fontTable.xml":
            override.set("ContentType", FONT_TABLE_CONTENT_TYPE)
            return _serialize_openxml(root, raw)
    ET.SubElement(root, f"{CT}Override", {"PartName": "/word/fontTable.xml", "ContentType": FONT_TABLE_CONTENT_TYPE})
    return _serialize_openxml(root, raw)


def _updated_document_rels(raw: bytes | None) -> bytes:
    if raw:
        root = ET.fromstring(raw)
    else:
        root = ET.Element(f"{PKG_REL}Relationships")
    for rel in root.findall(f"{PKG_REL}Relationship"):
        if rel.get("Type") == FONT_TABLE_REL_TYPE:
            rel.set("Target", "fontTable.xml")
            return _serialize_openxml(root, raw)
    used_ids = {rel.get("Id") for rel in root.findall(f"{PKG_REL}Relationship")}
    index = 1
    while f"rId{index}" in used_ids:
        index += 1
    ET.SubElement(root, f"{PKG_REL}Relationship", {"Id": f"rId{index}", "Type": FONT_TABLE_REL_TYPE, "Target": "fontTable.xml"})
    return _serialize_openxml(root, raw)


def _serialize_openxml(root: ET.Element, original_raw: bytes | None) -> bytes:
    if original_raw:
        nsmap = _namespace_map(original_raw)
        for prefix, uri in nsmap.items():
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass
        _clean_ignorable_prefixes(root, nsmap)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _namespace_map(raw: bytes) -> dict[str, str]:
    nsmap: dict[str, str] = {}
    try:
        for _, (prefix, uri) in ET.iterparse(BytesIO(raw), events=("start-ns",)):
            nsmap[prefix or ""] = uri
    except ET.ParseError:
        return nsmap
    return nsmap


def _clean_ignorable_prefixes(root: ET.Element, nsmap: dict[str, str]) -> None:
    ignorable_attr = f"{{{MC_NS}}}Ignorable"
    used_uris = _used_namespace_uris(root)
    for element in root.iter():
        value = element.get(ignorable_attr)
        if not value:
            continue
        kept = [prefix for prefix in value.split() if nsmap.get(prefix) in used_uris]
        if kept:
            element.set(ignorable_attr, " ".join(kept))
        else:
            element.attrib.pop(ignorable_attr, None)


def _used_namespace_uris(root: ET.Element) -> set[str]:
    uris: set[str] = set()
    for element in root.iter():
        for name in [element.tag, *element.attrib]:
            if name.startswith("{"):
                uris.add(name[1:].split("}", 1)[0])
    return uris


def _rewrite_docx(docx_path: Path, entries: dict[str, bytes], infos: dict[str, zipfile.ZipInfo]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f"{docx_path.stem}-", suffix=".docx", dir=str(docx_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                info = infos.get(name)
                if info is None:
                    zf.writestr(name, data)
                else:
                    zf.writestr(info, data)
        tmp_path.replace(docx_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
