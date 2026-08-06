"""
预处理入口
==========
Vibe Coder B | v1.0 | 2026-08-06

一站式预处理：格式检测 → 格式转换 → 结构解析 → 文档分块。

使用：
    from transagent.backend.pipeline.preprocess import preprocess
    result = preprocess("/path/to/doc.docx")
"""

import os
from transagent.interface import (
    FormatResult, ConvertResult, PreprocessResult, PlaceholderMap, Chunk, FormatType
)
from transagent.backend.pipeline.structure_parser import parse_structure
from transagent.backend.pipeline.chunker import chunk_document
from transagent.backend.config import get_config


def preprocess(file_path: str) -> PreprocessResult:
    """
    一站式预处理入口。

    流程: 格式检测 → MD转换 → 结构解析 → 文档分块

    Args:
        file_path: 用户上传文件路径

    Returns:
        PreprocessResult（受保护MD + chunk列表 + 占位符映射表）
    """
    # Step 1: 格式检测
    fmt = detect_format(file_path)

    # Step 2: 格式→MD
    converted = convert_to_md(file_path, fmt.format_type)

    # Step 3: 结构解析（占位符保护）
    protected_md, pmap = parse_structure(converted.md_text)

    # Step 4: 文档分块
    chunks = chunk_document(protected_md)

    return PreprocessResult(
        protected_md=protected_md,
        chunks=chunks,
        placeholder_map=pmap,
        token_estimate_total=sum(c.token_estimate for c in chunks),
        chunk_count=len(chunks),
    )


def detect_format(file_path: str) -> FormatResult:
    """检测文件格式"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    size = os.path.getsize(file_path)

    ext_map = {
        ".md": FormatType.MARKDOWN,
        ".markdown": FormatType.MARKDOWN,
        ".docx": FormatType.DOCX,
        ".doc": FormatType.DOCX,
        ".pdf": FormatType.PDF,
        ".txt": FormatType.TEXT,
        ".png": FormatType.IMAGE,
        ".jpg": FormatType.IMAGE,
        ".jpeg": FormatType.IMAGE,
    }

    format_type = ext_map.get(ext, FormatType.TEXT).value

    cfg = get_config().app
    if size > cfg.max_file_size_bytes:
        raise ValueError(f"文件过大: {size / 1024 / 1024:.1f}MB（上限{cfg.max_file_size_bytes / 1024 / 1024:.0f}MB）")

    page_count = None
    if format_type == FormatType.PDF.value:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            page_count = doc.page_count
            doc.close()
        except ImportError:
            pass

    return FormatResult(
        format_type=format_type,
        mime_type=f"application/{format_type}" if format_type != "text" else "text/plain",
        size_bytes=size,
        page_count=page_count,
        metadata={"extension": ext, "filename": os.path.basename(file_path)},
    )


def convert_to_md(file_path: str, format_type: str) -> ConvertResult:
    """
    任何格式→MD转换。

    P0支持: md(直接通过), txt(直接通过), docx(文本提取)
    P1支持: pdf(文本+嵌入图提取)
    """
    if format_type in (FormatType.MARKDOWN.value, FormatType.TEXT.value):
        with open(file_path, "r", encoding="utf-8") as f:
            md_text = f.read()
        return ConvertResult(
            md_text=md_text,
            assets_dir="",
            image_count=0,
            metadata={"converter": "passthrough"},
        )

    elif format_type == FormatType.DOCX.value:
        return _convert_docx(file_path)

    elif format_type == FormatType.PDF.value:
        return _convert_pdf(file_path)

    else:
        raise ValueError(f"不支持的格式: {format_type}（P0仅支持 md/txt/docx）")


def _convert_docx(file_path: str) -> ConvertResult:
    """docx→MD转换（P0核心功能）"""
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx未安装。pip install python-docx")

    cfg = get_config().app
    doc = Document(file_path)
    md_lines: list[str] = []
    image_count = 0
    assets_dir = os.path.join(cfg.workspace_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    for para in doc.paragraphs:
        # 检测标题
        if para.style.name.startswith("Heading"):
            level = int(para.style.name.split()[-1]) if para.style.name.split()[-1].isdigit() else 2
            md_lines.append(f"{'#' * level} {para.text}")
        else:
            md_lines.append(para.text)

    # 表格→MD管道表
    for table in doc.tables:
        md_lines.append("")
        for i, row in enumerate(table.rows):
            cells = [cell.text.replace("\n", " ") for cell in row.cells]
            md_lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                md_lines.append("|" + "|".join(["---"] * len(cells)) + "|")
        md_lines.append("")

    # 嵌入图片提取
    # 遍历 rels 提取图片
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            image_count += 1
            img_ext = os.path.splitext(rel.target_ref)[1]
            img_path = os.path.join(assets_dir, f"img_{image_count:02d}{img_ext}")
            with open(img_path, "wb") as f:
                f.write(rel.target_part.blob)

    return ConvertResult(
        md_text="\n\n".join(md_lines),
        assets_dir=assets_dir,
        image_count=image_count,
        metadata={"converter": "python-docx", "paragraphs": len(doc.paragraphs)},
    )


def _convert_pdf(file_path: str) -> ConvertResult:
    """PDF→MD转换（P1功能）"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PyMuPDF未安装。pip install PyMuPDF")

    doc = fitz.open(file_path)
    md_lines: list[str] = []

    for page in doc:
        text = page.get_text()
        md_lines.append(text)

    doc.close()
    return ConvertResult(
        md_text="\n\n".join(md_lines),
        assets_dir="",
        image_count=0,
        metadata={"converter": "PyMuPDF", "pages": len(md_lines)},
    )
