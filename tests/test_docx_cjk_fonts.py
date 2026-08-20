"""CJK font normalization tests."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from transagent.backend.pipeline import docx_cjk_fonts as fonts


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"


def _write_docx(path: Path, document_xml: str, font_table: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'><Override PartName='/word/document.xml' ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'/></Types>")
        zf.writestr("word/_rels/document.xml.rels", "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'/>")
        zf.writestr("word/document.xml", document_xml)
        if font_table is not None:
            zf.writestr("word/fontTable.xml", font_table)
    return path


def test_apply_cjk_fonts_updates_only_cjk_runs_and_preserves_latin_fonts(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="Noto Serif CJK SC", sans="Noto Sans CJK SC", source="test"))
    docx = _write_docx(
        tmp_path / "sample.docx",
        f"""<w:document xmlns:w="{W_NS}"><w:body>
<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:b/></w:rPr><w:t>中文正文</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:i/></w:rPr><w:t>中文标题</w:t></w:r></w:p>
<w:p><w:r><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/></w:rPr><w:t>English only</w:t></w:r></w:p>
</w:body></w:document>""",
        f"<w:fonts xmlns:w='{W_NS}'><w:font w:name='Times New Roman'/></w:fonts>",
    )

    result = fonts.apply_cjk_fonts(docx)
    assert result["applied"] is True
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
        runs = root.findall(f".//{W}r")
        body_fonts = runs[0].find(f"./{W}rPr/{W}rFonts")
        heading_fonts = runs[1].find(f"./{W}rPr/{W}rFonts")
        latin_fonts = runs[2].find(f"./{W}rPr/{W}rFonts")
        assert body_fonts.get(f"{W}ascii") == "Times New Roman"
        assert body_fonts.get(f"{W}hAnsi") == "Times New Roman"
        assert body_fonts.get(f"{W}eastAsia") == "Noto Serif CJK SC"
        assert runs[0].find(f"./{W}rPr/{W}b") is not None
        assert runs[0].find(f"./{W}rPr/{W}lang").get(f"{W}eastAsia") == "zh-CN"
        assert heading_fonts.get(f"{W}ascii") == "Arial"
        assert heading_fonts.get(f"{W}eastAsia") == "Noto Sans CJK SC"
        assert runs[1].find(f"./{W}rPr/{W}i") is not None
        assert latin_fonts.get(f"{W}eastAsia") is None
        font_table = ET.fromstring(zf.read("word/fontTable.xml"))
        font_names = {font.get(f"{W}name") for font in font_table.findall(f"{W}font")}
        assert {"Times New Roman", "Noto Serif CJK SC", "Noto Sans CJK SC"} <= font_names


def test_apply_cjk_fonts_fails_closed_when_font_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "font_available", lambda name: False)
    docx = _write_docx(
        tmp_path / "sample.docx",
        f"<w:document xmlns:w='{W_NS}'><w:body><w:p><w:r><w:t>中文</w:t></w:r></w:p></w:body></w:document>",
    )
    with pytest.raises(ValueError, match="DOCUMENT_RUNTIME_UNAVAILABLE"):
        fonts.apply_cjk_fonts(docx)


def test_apply_cjk_fonts_noops_without_cjk(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: (_ for _ in ()).throw(AssertionError("font lookup not needed")))
    docx = _write_docx(
        tmp_path / "sample.docx",
        f"<w:document xmlns:w='{W_NS}'><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>",
    )
    assert fonts.apply_cjk_fonts(docx) == {"applied": False, "reason": "no CJK text"}


def _document_runs(docx: Path):
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    return root.findall(f".//{W}r")


def _run_text(run: ET.Element) -> str:
    return "".join(node.text or "" for node in run.findall(f"{W}t"))


def test_pdf_force_render_font_splits_mixed_run_and_preserves_latin_fonts(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="Hiragino Sans GB", sans="Hiragino Sans GB", source="test"))
    docx = _write_docx(
        tmp_path / "sample.docx",
        f"""<w:document xmlns:w="{W_NS}"><w:body>
<w:p><w:r><w:rPr><w:rFonts w:ascii="LinuxLibertineG" w:hAnsi="LinuxLibertineG"/></w:rPr><w:t>中文 AWS</w:t></w:r></w:p>
</w:body></w:document>""",
    )
    fonts.apply_cjk_fonts(docx, force_render_font=True)
    runs = _document_runs(docx)
    assert [_run_text(run) for run in runs] == ["中文", " AWS"]
    cjk_fonts = runs[0].find(f"./{W}rPr/{W}rFonts")
    latin_fonts = runs[1].find(f"./{W}rPr/{W}rFonts")
    assert cjk_fonts.get(f"{W}eastAsia") == "Hiragino Sans GB"
    assert cjk_fonts.get(f"{W}ascii") == "Hiragino Sans GB"
    assert latin_fonts.get(f"{W}ascii") == "LinuxLibertineG"
    assert latin_fonts.get(f"{W}hAnsi") == "LinuxLibertineG"
    assert latin_fonts.get(f"{W}eastAsia") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("中文 AWS Kubernetes", ["中文", " AWS Kubernetes"]),
        ("AWS 中文 Kubernetes", ["AWS ", "中文", " Kubernetes"]),
        ("纯中文", ["纯中文"]),
        ("English only", ["English only"]),
    ],
)
def test_pdf_force_render_font_text_cases(monkeypatch, tmp_path, text, expected):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="Noto Serif CJK SC", sans="Noto Sans CJK SC", source="test"))
    docx = _write_docx(
        tmp_path / f"{len(text)}.docx",
        f"""<w:document xmlns:w="{W_NS}"><w:body>
<w:p><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr><w:t>{text}</w:t></w:r></w:p>
</w:body></w:document>""",
    )
    fonts.apply_cjk_fonts(docx, force_render_font=True)
    assert [_run_text(run) for run in _document_runs(docx)] == expected


def test_pdf_force_render_font_preserves_run_properties_and_spaces(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="CJK Serif", sans="CJK Serif", source="test"))
    docx = _write_docx(
        tmp_path / "styled.docx",
        f"""<w:document xmlns:w="{W_NS}"><w:body>
<w:p><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:i/><w:color w:val="FF0000"/><w:sz w:val="24"/><w:vertAlign w:val="superscript"/></w:rPr><w:t xml:space="preserve"> AWS 中文 Kubernetes </w:t></w:r></w:p>
</w:body></w:document>""",
    )
    fonts.apply_cjk_fonts(docx, force_render_font=True)
    runs = _document_runs(docx)
    assert [_run_text(run) for run in runs] == [" AWS ", "中文", " Kubernetes "]
    for run in runs:
        assert run.find(f"./{W}rPr/{W}b") is not None
        assert run.find(f"./{W}rPr/{W}i") is not None
        assert run.find(f"./{W}rPr/{W}color").get(f"{W}val") == "FF0000"
        assert run.find(f"./{W}rPr/{W}sz").get(f"{W}val") == "24"
        assert run.find(f"./{W}rPr/{W}vertAlign").get(f"{W}val") == "superscript"
    assert runs[0].find(f"{W}t").get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
    assert runs[2].find(f"{W}t").get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"


def test_pdf_force_render_font_handles_multiple_text_nodes(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="CJK Serif", sans="CJK Serif", source="test"))
    docx = _write_docx(
        tmp_path / "multi-text.docx",
        f"""<w:document xmlns:w="{W_NS}"><w:body>
<w:p><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr><w:t>AWS </w:t><w:t>中文</w:t><w:t> Kubernetes</w:t></w:r></w:p>
</w:body></w:document>""",
    )
    fonts.apply_cjk_fonts(docx, force_render_font=True)
    assert [_run_text(run) for run in _document_runs(docx)] == ["AWS ", "中文", " Kubernetes"]


def test_pdf_force_render_font_does_not_break_non_text_run(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "choose_cjk_fonts", lambda: fonts.CjkFontChoice(serif="CJK Serif", sans="CJK Serif", source="test"))
    docx = _write_docx(
        tmp_path / "drawing.docx",
        f"""<w:document xmlns:w="{W_NS}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>
<w:p><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr><w:t>AWS 中文</w:t><w:drawing><a:graphic/></w:drawing></w:r></w:p>
</w:body></w:document>""",
    )
    fonts.apply_cjk_fonts(docx, force_render_font=True)
    runs = _document_runs(docx)
    assert len(runs) == 1
    assert runs[0].find(f"{W}drawing") is not None
    rfonts = runs[0].find(f"./{W}rPr/{W}rFonts")
    assert rfonts.get(f"{W}ascii") == "Arial"
    assert rfonts.get(f"{W}hAnsi") == "Arial"
    assert rfonts.get(f"{W}eastAsia") == "CJK Serif"
