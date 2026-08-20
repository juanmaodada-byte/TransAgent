"""DOCX 结构快照工具。

用于 D2 Okapi Go/No-Go 探针前后的结构审计。只读取 DOCX ZIP/XML，
不重建文档，不参与翻译回填。
"""

from __future__ import annotations

import hashlib
import zipfile
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

NS = {
    "w": W_NS,
    "r": R_NS,
    "rel": PKG_REL_NS,
}


def snapshot_docx_structure(docx_path: str) -> dict:
    """返回稳定、可序列化的 DOCX 结构快照。"""
    with zipfile.ZipFile(docx_path) as zf:
        document_xml = ET.fromstring(zf.read("word/document.xml"))
        rels_xml = _read_optional_xml(zf, "word/_rels/document.xml.rels")

        media = []
        for name in sorted(n for n in zf.namelist() if n.startswith("word/media/")):
            media.append({
                "path": name,
                "sha256": hashlib.sha256(zf.read(name)).hexdigest(),
            })

    tables = document_xml.findall(".//w:tbl", NS)
    sections = document_xml.findall(".//w:sectPr", NS)

    rel_counts = {"header": 0, "footer": 0}
    if rels_xml is not None:
        for rel in rels_xml.findall("rel:Relationship", NS):
            rel_type = rel.get("Type", "")
            if rel_type.endswith("/header"):
                rel_counts["header"] += 1
            elif rel_type.endswith("/footer"):
                rel_counts["footer"] += 1

    return {
        "image_count": len(media),
        "images": media,
        "table_count": len(tables),
        "table_xml_counts": {
            "w_tbl": len(tables),
            "w_tr": len(document_xml.findall(".//w:tr", NS)),
            "tblGrid": len(document_xml.findall(".//w:tblGrid", NS)),
            "gridSpan": len(document_xml.findall(".//w:gridSpan", NS)),
            "vMerge": len(document_xml.findall(".//w:vMerge", NS)),
        },
        "section_count": len(sections),
        "header_footer_relationships": rel_counts,
    }


def _read_optional_xml(zf: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(zf.read(name))
    except KeyError:
        return None
