"""PDF-derived DOCX layout safety tests."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from transagent.backend.pipeline.docx_pdf_layout import WP_NS, make_pdf_drawings_inline


def test_pdf_anchors_are_converted_to_inline_drawings(tmp_path):
    docx = tmp_path / "pdf-normalized.docx"
    document_xml = f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<w:body><w:p><w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="1">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapSquare/>
<wp:extent cx="100" cy="100"/><wp:docPr id="1" name="Picture 1"/><a:graphic/>
</wp:anchor></w:drawing></w:r></w:p></w:body></w:document>"""
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)

    result = make_pdf_drawings_inline(docx)
    assert result.anchor_count_before == 1
    assert result.converted_to_inline == 1
    assert result.anchor_count_after == 0
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    assert list(root.iter(f"{{{WP_NS}}}anchor")) == []
    inline = next(root.iter(f"{{{WP_NS}}}inline"))
    child_names = [child.tag.rsplit("}", 1)[-1] for child in list(inline)]
    assert child_names == ["extent", "docPr", "graphic"]


def _write_docx_with_inline_image(docx_path, line: str, line_rule: str, cy: str) -> Path:
    """Build a PDF-normalized DOCX with one paragraph: exact line spacing + inline image."""
    document_xml = f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<w:body><w:p><w:pPr><w:spacing w:line="{line}" w:lineRule="{line_rule}"/></w:pPr>
<w:r><w:drawing><wp:inline><wp:extent cx="1000000" cy="{cy}"/>
<wp:docPr id="1" name="Picture 1"/><a:graphic/></wp:inline></w:drawing></w:r></w:p>
</w:body></w:document>"""
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
    return docx_path


def test_exact_line_spacing_relaxed_for_tall_inline_image(tmp_path):
    # Regression for Figure 1 in the Cloud stress PDF: pdf2docx placed a 5,261,110 EMU
    # inline image in a paragraph whose exact 220 twip line spacing (139,700 EMU) was
    # shorter than the image, so LibreOffice clipped the figure to a blank rectangle.
    docx = _write_docx_with_inline_image(tmp_path / "tall-image.docx", line="220", line_rule="exact", cy="5261110")
    result = make_pdf_drawings_inline(docx)
    assert result.line_spacing_fixed == 1
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    spacing = next(root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}spacing"))
    assert spacing.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}lineRule") == "atLeast"
    assert spacing.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}line") == "220"


def test_exact_line_spacing_kept_for_short_inline_image(tmp_path):
    # An inline image shorter than the exact line must not be relaxed.
    docx = _write_docx_with_inline_image(tmp_path / "short-image.docx", line="220", line_rule="exact", cy="10000")
    result = make_pdf_drawings_inline(docx)
    assert result.line_spacing_fixed == 0
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    spacing = next(root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}spacing"))
    assert spacing.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}lineRule") == "exact"


def test_at_least_line_spacing_left_untouched(tmp_path):
    docx = _write_docx_with_inline_image(tmp_path / "atleast.docx", line="220", line_rule="atLeast", cy="5261110")
    result = make_pdf_drawings_inline(docx)
    assert result.line_spacing_fixed == 0
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    spacing = next(root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}spacing"))
    assert spacing.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}lineRule") == "atLeast"


# ─────────────────────────────────────────────────────────────────────
# D10.1 order-fix regressions: anchors must be converted BEFORE the exact
# line-spacing relaxation runs, otherwise converted inline figures stay
# clipped to blank rectangles (Cloud identity Figure 1 evidence).
# ─────────────────────────────────────────────────────────────────────

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_docx_with_anchor(docx_path, *, line="220", line_rule="exact", cy="5261110",
                            cx="1000000", extra_inline_cy=None) -> Path:
    """Paragraph with exact spacing + one floating anchor (optionally an extra inline image)."""
    inline_extra = ""
    if extra_inline_cy is not None:
        inline_extra = (
            f'<w:r><w:drawing><wp:inline><wp:extent cx="1000000" cy="{extra_inline_cy}"/>'
            '<wp:docPr id="2" name="Picture 2"/><a:graphic/></wp:inline></w:drawing></w:r>'
        )
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<w:body><w:p><w:pPr><w:spacing w:line="{line}" w:lineRule="{line_rule}"/></w:pPr>
<w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="1">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapSquare/>
<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="Picture 1"/><a:graphic/>
</wp:anchor></w:drawing></w:r>{inline_extra}</w:p>
</w:body></w:document>"""
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
    return docx_path


def _read_document_root(docx_path):
    with zipfile.ZipFile(docx_path) as zf:
        return ET.fromstring(zf.read("word/document.xml"))


def test_anchor_tall_exact_relaxed_to_inline_at_least(tmp_path):
    # Order regression A: a tall floating anchor in an exact-spacing paragraph must be
    # converted to inline AND have its line spacing relaxed in the same pass.
    docx = _write_docx_with_anchor(tmp_path / "anchor-tall.docx", line="220", line_rule="exact", cy="5261110")
    result = make_pdf_drawings_inline(docx)
    assert result.anchor_count_before == 1
    assert result.converted_to_inline == 1
    assert result.anchor_count_after == 0
    assert result.line_spacing_fixed == 1
    root = _read_document_root(docx)
    spacing = next(root.iter(f"{{{W}}}spacing"))
    assert spacing.get(f"{{{W}}}lineRule") == "atLeast"
    assert spacing.get(f"{{{W}}}line") == "220"


def test_anchor_short_exact_kept_exact_after_conversion(tmp_path):
    # Order regression B: a short anchor must convert to inline but keep exact spacing.
    docx = _write_docx_with_anchor(tmp_path / "anchor-short.docx", line="220", line_rule="exact", cy="10000")
    result = make_pdf_drawings_inline(docx)
    assert result.converted_to_inline == 1
    assert result.line_spacing_fixed == 0
    root = _read_document_root(docx)
    spacing = next(root.iter(f"{{{W}}}spacing"))
    assert spacing.get(f"{{{W}}}lineRule") == "exact"


def test_multiple_anchors_same_paragraph_fixed_once(tmp_path):
    # Order regression C: two anchors and one pre-existing tall inline in the same
    # paragraph; every anchor converts but the spacing fix counts the paragraph once.
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<w:body><w:p><w:pPr><w:spacing w:line="220" w:lineRule="exact"/></w:pPr>
<w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="1">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapSquare/>
<wp:extent cx="1000000" cy="5261110"/><wp:docPr id="1" name="Picture 1"/><a:graphic/>
</wp:anchor></w:drawing></w:r>
<w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="2">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapSquare/>
<wp:extent cx="1000000" cy="5261110"/><wp:docPr id="2" name="Picture 2"/><a:graphic/>
</wp:anchor></w:drawing></w:r>
<w:r><w:drawing><wp:inline><wp:extent cx="1000000" cy="5261110"/>
<wp:docPr id="3" name="Picture 3"/><a:graphic/></wp:inline></w:drawing></w:r></w:p>
</w:body></w:document>"""
    docx = tmp_path / "multi-anchor.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
    result = make_pdf_drawings_inline(docx)
    assert result.anchor_count_before == 2
    assert result.converted_to_inline == 2
    assert result.anchor_count_after == 0
    assert result.line_spacing_fixed == 1
    root = _read_document_root(docx)
    inlines = list(root.iter(f"{{{WP_NS}}}inline"))
    assert len(inlines) == 3
    spacing = next(root.iter(f"{{{W}}}spacing"))
    assert spacing.get(f"{{{W}}}lineRule") == "atLeast"


def test_existing_inline_and_newly_converted_inline_both_covered(tmp_path):
    # Order regression D: a pre-existing tall inline PLUS a newly converted tall anchor
    # in the same paragraph are both present after one pass, with the exact spacing
    # relaxed exactly once (fix must run after conversion, not before).
    docx = _write_docx_with_anchor(
        tmp_path / "mixed.docx", line="220", line_rule="exact", cy="5261110", extra_inline_cy="5261110"
    )
    result = make_pdf_drawings_inline(docx)
    assert result.anchor_count_before == 1
    assert result.converted_to_inline == 1
    assert result.line_spacing_fixed == 1
    root = _read_document_root(docx)
    inlines = list(root.iter(f"{{{WP_NS}}}inline"))
    assert len(inlines) == 2
    spacing = next(root.iter(f"{{{W}}}spacing"))
    assert spacing.get(f"{{{W}}}lineRule") == "atLeast"


def test_conversion_preserves_extent_and_other_package_parts(tmp_path):
    # Order regression E: the converted inline keeps the anchor's extent and the
    # non-document package parts (rels, media) stay byte-identical.
    media = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body><w:p><w:pPr><w:spacing w:line="240" w:lineRule="exact"/></w:pPr>
<w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="1">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapSquare/>
<wp:extent cx="3333000" cy="2000000"/><wp:docPr id="1" name="Picture 1"/>
<a:graphic><a:blip r:embed="rId5"/></a:graphic></wp:anchor></w:drawing></w:r></w:p>
</w:body></w:document>"""
    rels_xml = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
 Target="media/image1.png"/></Relationships>"""
    docx = tmp_path / "package-integrity.docx"
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/media/image1.png", media)
    with zipfile.ZipFile(docx) as zf:
        before_parts = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    result = make_pdf_drawings_inline(docx)

    assert result.converted_to_inline == 1
    assert result.line_spacing_fixed == 1
    with zipfile.ZipFile(docx) as zf:
        after_names = set(zf.namelist())
        after_parts = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        root = ET.fromstring(zf.read("word/document.xml"))
    assert after_names == set(before_parts) | {"word/document.xml"}
    assert after_parts == before_parts
    inline = next(root.iter(f"{{{WP_NS}}}inline"))
    extent = inline.find(f"{{{WP_NS}}}extent")
    assert extent is not None
    assert extent.get("cx") == "3333000"
    assert extent.get("cy") == "2000000"
    spacing = next(root.iter(f"{{{W}}}spacing"))
    assert spacing.get(f"{{{W}}}line") == "240"
    assert spacing.get(f"{{{W}}}lineRule") == "atLeast"


# ─────────────────────────────────────────────────────────────────────
# D10.1 ghost-frame regression: pdf2docx emits a caption table whose exact
# row height equals the floating figure's height and whose paragraph `before`
# spacing pushes the caption to the cell bottom. After anchor->inline the image
# flows normally, but the caption table keeps the figure-height row + border,
# leaving a near-full-page empty box. The fix must collapse only such a table.
# ─────────────────────────────────────────────────────────────────────

def _write_anchor_with_caption_table(docx_path, *, media: bytes = b"") -> None:
    """DOCX with: an anchor figure (cy=5261110 EMU), a caption table whose exact row
    height (8286 twips) equals that image height with a big `before` spacing, and a
    normal table whose exact row height (500 twips) must be left untouched."""
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>
<w:p><w:pPr><w:spacing w:line="220" w:lineRule="exact"/></w:pPr>
<w:r><w:drawing><wp:anchor distT="0" distB="0" relativeHeight="1">
<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="page"/><wp:wrapNone/>
<wp:extent cx="4652009" cy="5261110"/><wp:docPr id="1" name="Picture 1"/>
<a:graphic><a:blip r:embed="rId5"/></a:graphic></wp:anchor></w:drawing></w:r></w:p>
<w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr><w:tblGrid><w:gridCol w:w="10555"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="8286"/></w:trPr>
<w:tc><w:tcPr><w:tcBorders><w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>
<w:start w:val="single" w:sz="4"/><w:end w:val="single" w:sz="4"/></w:tcBorders></w:tcPr>
<w:p><w:pPr><w:spacing w:line="288" w:lineRule="auto" w:before="7984" w:after="0"/></w:pPr>
<w:r><w:t>Figure 1: caption</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr><w:tblGrid><w:gridCol w:w="10555"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="500"/></w:trPr>
<w:tc><w:tcPr><w:tcBorders><w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/></w:tcBorders></w:tcPr>
<w:p><w:r><w:t>normal table data</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    rels_xml = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
 Target="media/image1.png"/></Relationships>"""
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        if media:
            zf.writestr("word/media/image1.png", media)


def test_anchor_caption_table_collapses_but_normal_table_is_preserved(tmp_path):
    media = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    docx = tmp_path / "ghost-frame.docx"
    _write_anchor_with_caption_table(docx, media=media)
    with zipfile.ZipFile(docx) as zf:
        before_parts = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}

    result = make_pdf_drawings_inline(docx)
    assert result.converted_to_inline == 1

    with zipfile.ZipFile(docx) as zf:
        after_parts = {name: zf.read(name) for name in zf.namelist() if name != "word/document.xml"}
        root = ET.fromstring(zf.read("word/document.xml"))
    # media / relationships unchanged
    assert after_parts == before_parts

    W_NS_FULL = f"{{{W}}}"
    # anchor converted to inline with unchanged extent
    inline = next(root.iter(f"{{{WP_NS}}}inline"))
    extent = inline.find(f"{{{WP_NS}}}extent")
    assert extent.get("cx") == "4652009"
    assert extent.get("cy") == "5261110"

    # caption table (row height == image height) collapsed: no exact row remains there
    rows = list(root.iter(f"{W_NS_FULL}tr"))
    caption_tr = rows[0]
    normal_tr = rows[1]
    caption_height = caption_tr.find(f"{W_NS_FULL}trPr/{W_NS_FULL}trHeight")
    assert caption_height is None or caption_height.get(f"{W_NS_FULL}hRule") != "exact"
    caption_spacing = next(caption_tr.iter(f"{W_NS_FULL}spacing"))
    assert caption_spacing.get(f"{W_NS_FULL}before") in (None, "0")

    # normal table border must NOT be touched by the ghost-frame collapse. Its row
    # carries body text, so the two-column/row-height fix relaxes exact -> atLeast
    # (value preserved); the border must survive both passes unchanged.
    normal_height = normal_tr.find(f"{W_NS_FULL}trPr/{W_NS_FULL}trHeight")
    assert normal_height is not None
    assert normal_height.get(f"{W_NS_FULL}hRule") == "atLeast"
    assert normal_height.get(f"{W_NS_FULL}val") == "500"
    normal_borders = normal_tr.find(f".//{W_NS_FULL}tcBorders")
    assert normal_borders is not None


def _write_pagination_footer_docx(docx_path) -> None:
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>
<w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr><w:tblGrid><w:gridCol w:w="10555"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="2450"/></w:trPr>
<w:tc><w:tcPr><w:tcBorders><w:bottom w:val="single" w:sz="8"/></w:tcBorders></w:tcPr>
<w:p><w:r><w:t>ABSTRACT Cloud computing and microservices architecture is a body of content.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr><w:tblGrid><w:gridCol w:w="5298"/><w:gridCol w:w="5298"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="324"/></w:trPr>
<w:tc><w:tcPr><w:tcBorders><w:top w:val="single" w:sz="8"/></w:tcBorders></w:tcPr>
<w:p><w:r><w:t>J Arti Inte &amp; Cloud Comp, 2024</w:t></w:r></w:p></w:tc>
<w:tc><w:tcPr><w:tcBorders><w:top w:val="single" w:sz="8"/></w:tcBorders></w:tcPr>
<w:p><w:r><w:t>Volume 3(4): 2-5</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)


def test_pagination_footer_rows_removed_but_body_table_preserved(tmp_path):
    docx = tmp_path / "pagination-footer.docx"
    _write_pagination_footer_docx(docx)
    make_pdf_drawings_inline(docx)

    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))

    W_NS_FULL = f"{{{W}}}"
    tables = list(root.iter(f"{W_NS_FULL}tbl"))
    # The footer-only table (two short columns, top border, 324 twips) is removed, leaving
    # the ABSTRACT body table intact.
    texts = ["".join(t.itertext()).strip() for t in tables]
    assert any("ABSTRACT Cloud computing" in t for t in texts)
    assert not any("Volume 3(4): 2-5" in t for t in texts)


# ─────────────────────────────────────────────────────────────────────
# Two-column spacing + content-row height regressions: pdf2docx emits
# <w:cols num="2" equalWidth="0"/> without w:space or <w:col> widths, and
# content-table rows with exact heights that Chinese translations overflow.
# ─────────────────────────────────────────────────────────────────────

W_NS_FULL = f"{{{W}}}"


def test_pdf_columns_get_space_and_equal_width(tmp_path):
    docx = tmp_path / "two-col.docx"
    document_xml = f"""<w:document xmlns:w="{W}">
<w:body><w:p><w:pPr><w:sectPr>
<w:cols w:num="2" w:equalWidth="0"/></w:sectPr></w:pPr></w:p>
<w:p><w:r><w:t>two-column body</w:t></w:r></w:p></w:body></w:document>"""
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)

    make_pdf_drawings_inline(docx)

    root = _read_document_root(docx)
    cols = next(root.iter(f"{W_NS_FULL}cols"))
    assert cols.get(f"{W_NS_FULL}space") == "360"
    assert cols.get(f"{W_NS_FULL}equalWidth") == "1"


def test_content_table_row_height_relaxed_to_at_least(tmp_path):
    docx = tmp_path / "content-row.docx"
    document_xml = f"""<w:document xmlns:w="{W}">
<w:body><w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="10555"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="500"/></w:trPr>
<w:tc><w:tcPr><w:tcBorders><w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/></w:tcBorders></w:tcPr>
<w:p><w:r><w:t>content text that will be translated</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)

    make_pdf_drawings_inline(docx)

    root = _read_document_root(docx)
    tr_height = next(root.iter(f"{W_NS_FULL}trHeight"))
    assert tr_height.get(f"{W_NS_FULL}hRule") == "atLeast"
    assert tr_height.get(f"{W_NS_FULL}val") == "500"


def test_image_table_row_height_not_relaxed(tmp_path):
    docx = tmp_path / "image-row.docx"
    document_xml = f"""<w:document xmlns:w="{W}"
 xmlns:wp="{WP_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
<w:body><w:tbl><w:tblPr><w:tblW w:type="auto" w:w="0"/></w:tblPr>
<w:tblGrid><w:gridCol w:w="10555"/></w:tblGrid>
<w:tr><w:trPr><w:trHeight w:hRule="exact" w:val="500"/></w:trPr>
<w:tc><w:p><w:r><w:drawing><wp:inline><wp:extent cx="1000000" cy="500000"/>
<wp:docPr id="1" name="Picture 1"/><a:graphic/></wp:inline></w:drawing></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""
    with zipfile.ZipFile(docx, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)

    make_pdf_drawings_inline(docx)

    root = _read_document_root(docx)
    tr_height = next(root.iter(f"{W_NS_FULL}trHeight"))
    assert tr_height.get(f"{W_NS_FULL}hRule") == "exact"
