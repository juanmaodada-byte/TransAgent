"""D2 XLIFF pseudo-translation contract tests."""

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from scripts.d2_okapi_probe import ProbeError, XLIFF_NS, pseudo_translate_xliff, qname


def write_xliff(path: Path, units: str) -> Path:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<xliff xmlns="{XLIFF_NS}" version="1.2">
  <file original="okapi_probe_mixed.docx" source-language="en" target-language="zh-CN">
    <body>{units}</body>
  </file>
</xliff>
""",
        encoding="utf-8",
    )
    return path


def targets(path: Path):
    root = ET.parse(path).getroot()
    ns = {"x": XLIFF_NS}
    return root.findall(".//x:target", ns)


def test_pseudo_translate_adds_namespaced_target_when_missing(tmp_path):
    path = write_xliff(tmp_path / "sample.xlf", '<trans-unit id="u1"><source>Hello</source></trans-unit>')
    stats = pseudo_translate_xliff(path)
    target = targets(path)[0]
    assert stats.unit_count == 1
    assert target.tag == qname(XLIFF_NS, "target")
    assert target.text == "[ZH] Hello"


def test_pseudo_translate_replaces_existing_target(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>Hello</source><target>Old</target></trans-unit>',
    )
    pseudo_translate_xliff(path)
    assert targets(path)[0].text == "[ZH] Hello"


def test_pseudo_translate_preserves_g_inline_and_tail(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A <g id="1">bold</g> tail</source></trans-unit>',
    )
    stats = pseudo_translate_xliff(path)
    target = targets(path)[0]
    child = list(target)[0]
    assert target.text == "[ZH] A "
    assert child.tag == qname(XLIFF_NS, "g")
    assert child.text == "bold"
    assert child.tail == " tail"
    assert stats.inline_type_counts == {"g": 1}


def test_pseudo_translate_preserves_self_closing_x_and_ph(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>Run <x id="1"/> then <ph id="2">&lt;br/&gt;</ph>.</source></trans-unit>',
    )
    stats = pseudo_translate_xliff(path)
    assert stats.inline_type_counts == {"ph": 1, "x": 1}
    target = targets(path)[0]
    assert [child.tag for child in list(target)] == [qname(XLIFF_NS, "x"), qname(XLIFF_NS, "ph")]
    assert list(target)[0].tail == " then "
    assert list(target)[1].tail == "."


def test_pseudo_translate_does_not_copy_source_tail(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>Hello</source>source-tail</trans-unit>',
    )
    pseudo_translate_xliff(path)
    assert targets(path)[0].tail is None


def test_pseudo_translate_rejects_zero_units(tmp_path):
    path = write_xliff(tmp_path / "sample.xlf", "")
    with pytest.raises(ProbeError, match="zero trans-unit"):
        pseudo_translate_xliff(path)


def test_pseudo_translate_rejects_duplicate_unit_ids(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source></trans-unit><trans-unit id="u1"><source>B</source></trans-unit>',
    )
    with pytest.raises(ProbeError, match="duplicate"):
        pseudo_translate_xliff(path)


def test_pseudo_translate_rejects_missing_unit_id(tmp_path):
    path = write_xliff(tmp_path / "sample.xlf", "<trans-unit><source>A</source></trans-unit>")
    with pytest.raises(ProbeError, match="missing id"):
        pseudo_translate_xliff(path)


def test_pseudo_translate_rejects_seg_source_or_mrk(tmp_path):
    path = write_xliff(
        tmp_path / "sample.xlf",
        '<trans-unit id="u1"><source>A</source><seg-source><mrk mtype="seg">A</mrk></seg-source></trans-unit>',
    )
    with pytest.raises(ProbeError, match="seg-source/mrk"):
        pseudo_translate_xliff(path)
