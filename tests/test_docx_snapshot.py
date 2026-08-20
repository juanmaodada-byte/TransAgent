"""DOCX structure snapshot tests for Okapi Go/No-Go preparation."""

from transagent.backend.pipeline.docx_snapshot import snapshot_docx_structure


def test_docx_snapshot_is_stable(okapi_probe_docx_path):
    first = snapshot_docx_structure(okapi_probe_docx_path)
    second = snapshot_docx_structure(okapi_probe_docx_path)
    assert first == second


def test_docx_snapshot_records_required_structure(okapi_probe_docx_path):
    snap = snapshot_docx_structure(okapi_probe_docx_path)
    assert snap["image_count"] == 1
    assert len(snap["images"]) == 1
    assert len(snap["images"][0]["sha256"]) == 64
    assert snap["table_count"] == 1
    assert snap["table_xml_counts"]["w_tbl"] == 1
    assert snap["table_xml_counts"]["w_tr"] >= 4
    assert snap["table_xml_counts"]["tblGrid"] >= 1
    assert snap["table_xml_counts"]["gridSpan"] >= 1
    assert snap["table_xml_counts"]["vMerge"] >= 1
    assert snap["section_count"] >= 1
    assert snap["header_footer_relationships"]["header"] == 1
    assert snap["header_footer_relationships"]["footer"] == 1
