"""PDF-derived DOCX layout safety fixes."""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.backend.pipeline.docx_cjk_fonts import _serialize_openxml


WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WP = f"{{{WP_NS}}}"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"

# 1 twip = 1/1440 inch, 1 inch = 914400 EMU
EMU_PER_TWIP = 914400 / 1440

# pdf2docx lifts the source PDF's running page footer (journal name + "Volume X(N): page")
# into the body as a two-column, very short, top-border-only table row. When such a row ends
# up as the last row of a body table that straddles a page break, it can be orphaned onto a
# page by itself, producing a body-blank page. These rows are footer chrome, not article
# content, so they are removed. The threshold leaves a wide margin: footer rows are 306-324
# twips tall while the smallest real content row is 1040 twips.
PAGINATION_MAX_TR_HEIGHT_TWIPS = 400
PAGINATION_MAX_TEXT_LEN = 120


@dataclass(frozen=True)
class PdfLayoutFixResult:
    anchor_count_before: int
    anchor_count_after: int
    converted_to_inline: int
    line_spacing_fixed: int = 0

    def to_public_dict(self) -> dict:
        return {
            "anchor_count_before": self.anchor_count_before,
            "anchor_count_after": self.anchor_count_after,
            "converted_to_inline": self.converted_to_inline,
            "line_spacing_fixed": self.line_spacing_fixed,
        }


def make_pdf_drawings_inline(docx_path: Path) -> PdfLayoutFixResult:
    """Convert floating drawing anchors to inline drawings for PDF-normalized DOCX."""
    docx_path = docx_path.resolve()
    if not zipfile.is_zipfile(docx_path):
        return PdfLayoutFixResult(0, 0, 0)

    with zipfile.ZipFile(docx_path) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
        infos = {info.filename: info for info in zf.infolist()}

    before = 0
    converted = 0
    spacing_fixed = 0
    placeholder_collapsed = 0
    pagination_removed = 0
    columns_fixed = 0
    row_heights_relaxed = 0
    for name in sorted(entries):
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        try:
            root = ET.fromstring(entries[name])
        except ET.ParseError:
            continue
        anchors = list(root.iter(f"{WP}anchor"))
        part_converted = 0
        converted_heights: list[int] = []
        if anchors:
            before += len(anchors)
            part_converted, converted_heights = _convert_anchors_in_parent(root)
            converted += part_converted
        # Run the line-spacing fix after anchor->inline conversion so that
        # newly converted inline images are also covered (exact line spacing
        # would otherwise clip them, producing blank figure rectangles).
        part_spacing_fixed = _fix_inline_image_line_spacing(root)
        spacing_fixed += part_spacing_fixed
        # Collapse any caption table whose exact row height was sized to a converted
        # floating figure's height (the figure now flows inline, so the placeholder
        # height would render as a huge empty bordered box).
        part_placeholder_collapsed = _collapse_figure_placeholder_containers(root, converted_heights)
        placeholder_collapsed += part_placeholder_collapsed
        # Remove running page-footer rows lifted into the body by pdf2docx (they can be
        # orphaned onto their own page and trip the body-blank gate).
        part_pagination_removed = _remove_pagination_footers(root)
        pagination_removed += part_pagination_removed
        # pdf2docx leaves multi-column section definitions missing column spacing/width,
        # and writes content-table rows with exact heights that Chinese translations can
        # overflow. Fix both after anchor/ghost/pagination handling.
        part_columns_fixed = _fix_pdf_columns(root)
        columns_fixed += part_columns_fixed
        part_row_heights_relaxed = _relax_content_table_row_heights(root)
        row_heights_relaxed += part_row_heights_relaxed
        if (
            part_spacing_fixed
            or anchors
            or part_placeholder_collapsed
            or part_pagination_removed
            or part_columns_fixed
            or part_row_heights_relaxed
        ):
            entries[name] = _serialize_openxml(root, entries[name])

    if converted or spacing_fixed or placeholder_collapsed or pagination_removed or columns_fixed or row_heights_relaxed:
        _rewrite_docx(docx_path, entries, infos)

    after = _count_anchors(entries if not (converted or spacing_fixed) else _read_entries(docx_path))
    return PdfLayoutFixResult(
        anchor_count_before=before,
        anchor_count_after=after,
        converted_to_inline=converted,
        line_spacing_fixed=spacing_fixed,
    )


def _fix_inline_image_line_spacing(root: ET.Element) -> int:
    """Relax exact line spacing for paragraphs with inline images taller than the line.

    LibreOffice clips inline pictures inside paragraphs that use fixed (``exact``)
    line spacing. pdf2docx can place large PDF-extracted figures into such paragraphs,
    which makes the figure render as a blank rectangle even though the image file and
    relationships are intact. Setting the rule to ``atLeast`` lets the line grow to
    fit the image while keeping the exact value as a minimum.
    """
    changed = 0
    for para in root.iter(f"{W}p"):
        ppr = para.find(f"{W}pPr")
        if ppr is None:
            continue
        spacing = ppr.find(f"{W}spacing")
        if spacing is None or spacing.get(f"{W}lineRule") != "exact":
            continue
        line_twips = spacing.get(f"{W}line")
        try:
            line_emu = int(line_twips) * EMU_PER_TWIP if line_twips else 0.0
        except ValueError:
            line_emu = 0.0
        for inline in para.iter(f"{WP}inline"):
            extent = inline.find(f"{WP}extent")
            if extent is None:
                continue
            cy = extent.get("cy")
            try:
                cy_emu = int(cy) if cy else 0
            except ValueError:
                continue
            if cy_emu > line_emu:
                spacing.set(f"{W}lineRule", "atLeast")
                changed += 1
                break
    return changed


def _convert_anchors_in_parent(root: ET.Element) -> tuple[int, list[int]]:
    """Convert floating anchors to inline drawings; return (count, image heights in EMU)."""
    converted = 0
    heights: list[int] = []
    for parent in root.iter():
        children = list(parent)
        for index, child in enumerate(children):
            if child.tag == f"{WP}anchor":
                heights.append(_anchor_cy_emu(child))
                parent.remove(child)
                parent.insert(index, _anchor_to_inline(child))
                converted += 1
    return converted, heights


def _anchor_cy_emu(anchor: ET.Element) -> int:
    extent = anchor.find(f"{WP}extent")
    if extent is None:
        return 0
    try:
        return int(extent.get("cy") or 0)
    except ValueError:
        return 0


def _collapse_figure_placeholder_containers(root: ET.Element, converted_heights_emu: list[int]) -> int:
    """Collapse caption tables whose exact row height was sized to a converted figure.

    pdf2docx places a floating figure's caption inside a single-cell bordered table whose
    ``trHeight`` (exact) equals the figure's height and whose paragraph ``before`` spacing
    pushes the caption to the cell bottom. Once the figure becomes inline, that table keeps
    its figure-height row and renders as a near-full-page empty box. Only tables whose exact
    row height matches a converted figure's height (within 1% for twip rounding) AND whose
    cell contains no drawing are collapsed. Normal tables are never touched.
    """
    if not converted_heights_emu:
        return 0
    collapsed = 0
    for tbl in root.iter(f"{W}tbl"):
        for tr in tbl.findall(f"{W}tr"):
            tr_pr = tr.find(f"{W}trPr")
            if tr_pr is None:
                continue
            tr_height = tr_pr.find(f"{W}trHeight")
            if tr_height is None or tr_height.get(f"{W}hRule") != "exact":
                continue
            try:
                row_emu = int(tr_height.get(f"{W}val") or 0) * EMU_PER_TWIP
            except ValueError:
                continue
            if row_emu <= 0:
                continue
            if not any(abs(row_emu - h) <= h * 0.01 for h in converted_heights_emu if h > 0):
                continue
            tc = tr.find(f"{W}tc")
            if tc is not None and (
                tc.find(f".//{WP}inline") is not None or tc.find(f".//{WP}anchor") is not None
            ):
                continue
            tr_pr.remove(tr_height)
            _zero_paragraph_before(tr)
            collapsed += 1
    return collapsed


def _zero_paragraph_before(tr: ET.Element) -> None:
    """Zero the paragraph ``before`` spacing used to push a caption to the cell bottom."""
    for para in tr.iter(f"{W}p"):
        p_pr = para.find(f"{W}pPr")
        if p_pr is None:
            continue
        spacing = p_pr.find(f"{W}spacing")
        if spacing is None:
            continue
        before = spacing.get(f"{W}before")
        if before:
            try:
                if int(before) > 0:
                    spacing.set(f"{W}before", "0")
            except ValueError:
                continue


def _is_pagination_footer_row(tr: ET.Element) -> bool:
    """True if a table row is a pdf2docx running page footer (two-column, very short,
    top-border-only, exact short height)."""
    tr_pr = tr.find(f"{W}trPr")
    if tr_pr is None:
        return False
    tr_height = tr_pr.find(f"{W}trHeight")
    if tr_height is None or tr_height.get(f"{W}hRule") != "exact":
        return False
    try:
        height = int(tr_height.get(f"{W}val") or 0)
    except ValueError:
        return False
    if height > PAGINATION_MAX_TR_HEIGHT_TWIPS:
        return False
    cells = tr.findall(f"{W}tc")
    if len(cells) != 2:
        return False
    for cell in cells:
        if cell.find(f"{W}tcPr/{W}gridSpan") is not None:
            return False
        borders = cell.find(f"{W}tcPr/{W}tcBorders")
        if borders is None or borders.find(f"{W}top") is None:
            return False
        for side in ("bottom", "start", "end"):
            if borders.find(f"{W}{side}") is not None:
                return False
    text = "".join(tr.itertext()).strip()
    if not text or len(text) >= PAGINATION_MAX_TEXT_LEN:
        return False
    return True


def _remove_pagination_footers(root: ET.Element) -> int:
    """Remove running page-footer rows (and any table left empty by the removal)."""
    removed = 0
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for tbl in list(root.iter(f"{W}tbl")):
        for tr in list(tbl.findall(f"{W}tr")):
            if _is_pagination_footer_row(tr):
                tbl.remove(tr)
                removed += 1
        if len(tbl.findall(f"{W}tr")) == 0:
            parent = parent_map.get(tbl)
            if parent is not None:
                parent.remove(tbl)
                removed += 1
    return removed


COLUMN_SPACE_TWIPS = "360"


def _fix_pdf_columns(root: ET.Element) -> int:
    """Fix pdf2docx multi-column section definitions.

    pdf2docx emits ``<w:cols w:num="2" w:equalWidth="0"/>`` without a column gap
    (``w:space``) or explicit ``<w:col>`` widths, so the two columns render flush
    together. For academic two-column layouts we set a 0.25-inch gap and force
    equal-width columns, dropping any explicit ``<w:col>`` children (which would
    conflict with ``equalWidth="1"``). Single-column sections are left untouched.
    """
    changed = 0
    for cols in root.iter(f"{W}cols"):
        try:
            num = int(cols.get(f"{W}num") or 0)
        except ValueError:
            num = 0
        if num < 2:
            continue
        if cols.get(f"{W}space") != COLUMN_SPACE_TWIPS:
            cols.set(f"{W}space", COLUMN_SPACE_TWIPS)
            changed += 1
        if cols.get(f"{W}equalWidth") != "1":
            cols.set(f"{W}equalWidth", "1")
            changed += 1
        for col in cols.findall(f"{W}col"):
            cols.remove(col)
            changed += 1
    return changed


def _tr_has_body_text(tr: ET.Element) -> bool:
    """True if any cell of the row contains non-empty body text (``<w:t>``)."""
    for tc in tr.findall(f"{W}tc"):
        for t in tc.iter(f"{W}t"):
            if t.text and t.text.strip():
                return True
    return False


def _relax_content_table_row_heights(root: ET.Element) -> int:
    """Relax exact row heights on content rows so longer translations can reflow.

    pdf2docx writes content-table rows with ``w:hRule="exact"`` (a fixed height).
    Chinese translations are typically longer than the English source, so the text
    overflows the fixed row and is clipped at page breaks ("half-box" rows) or leaves
    large gaps. Rows that carry body text get ``atLeast`` (keeping ``w:val`` as a
    minimum); image-only / empty / decorative rows keep ``exact``. Borders and the
    ``trHeight`` element itself are preserved.
    """
    changed = 0
    for tr in root.iter(f"{W}tr"):
        tr_pr = tr.find(f"{W}trPr")
        if tr_pr is None:
            continue
        tr_height = tr_pr.find(f"{W}trHeight")
        if tr_height is None or tr_height.get(f"{W}hRule") != "exact":
            continue
        if not _tr_has_body_text(tr):
            continue
        tr_height.set(f"{W}hRule", "atLeast")
        changed += 1
    return changed


def _anchor_to_inline(anchor: ET.Element) -> ET.Element:
    inline = ET.Element(f"{WP}inline")
    for child in list(anchor):
        local = child.tag.rsplit("}", 1)[-1] if child.tag.startswith("{") else child.tag
        if local in {"extent", "effectExtent", "docPr", "cNvGraphicFramePr", "graphic"}:
            inline.append(child)
    return inline


def _read_entries(docx_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(docx_path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _count_anchors(entries: dict[str, bytes]) -> int:
    count = 0
    for name, raw in entries.items():
        if name.startswith("word/") and name.endswith(".xml"):
            try:
                count += sum(1 for _ in ET.fromstring(raw).iter(f"{WP}anchor"))
            except ET.ParseError:
                pass
    return count


def _rewrite_docx(docx_path: Path, entries: dict[str, bytes], infos: dict[str, zipfile.ZipInfo]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f"{docx_path.stem}-layout-", suffix=".docx", dir=str(docx_path.parent))
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
