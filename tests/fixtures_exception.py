"""
异常 Fixtures
=============
D1 测试基础设施 — 用于验证错误处理和边界条件的测试数据。

不依赖 API key、网络或真实 LLM。
"""
import os
import pytest
from io import BytesIO

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")


# ══════════════════════════════════════════════════════════════════
# 格式异常 fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def unsupported_ext_file(tmp_path):
    """未支持的扩展名文件"""
    f = tmp_path / "test.xyz"
    f.write_text("hello world")
    return str(f)


@pytest.fixture
def corrupt_docx_file(tmp_path):
    """损坏的 DOCX（非 ZIP 头）"""
    f = tmp_path / "corrupt.docx"
    f.write_bytes(b"this is not a valid ZIP file")
    return str(f)


@pytest.fixture
def illegal_txt_encoding(tmp_path):
    """非法编码的 TXT 文件（latin-1 的 invalid bytes）"""
    f = tmp_path / "bad_encoding.txt"
    # 写入非 UTF-8 bytes
    f.write_bytes(b'\xff\xfe\x00\x00invalid utf8 bytes\x80\x81\x82')
    return str(f)


@pytest.fixture
def dot_doc_file(tmp_path):
    """.doc 文件（旧格式，不是 DOCX）"""
    f = tmp_path / "legacy.doc"
    f.write_bytes(b'\xd0\xcf\x11\xe0' + b'\x00' * 100)  # OLE2 header
    return str(f)


# ══════════════════════════════════════════════════════════════════
# 占位符异常 fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def missing_placeholder_text():
    """包含不存在的占位符引用的文本"""
    from TransAgent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "original text"
    text = "Some text with {NT_0} and a missing {NT_99}"
    return text, pmap


@pytest.fixture
def duplicate_placeholder_text():
    """还原后仍有残留占位符的文本"""
    from TransAgent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "code block"
    # 文本中有重复的 NT_0
    text = "Here {NT_0} and here {NT_0} again"
    return text, pmap


@pytest.fixture
def unknown_placeholder_text():
    """包含未在 pmap 中定义的占位符"""
    from TransAgent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "known"
    text = "Known {NT_0} and unknown {NT_X}"
    return text, pmap


@pytest.fixture
def spaced_placeholder_text():
    """被 LLM 插入空格的占位符"""
    # {NT_0} 被改写为 { NT_0 } 或 {NT_ 0}
    text = "Broken { NT_0 } and { NT_0} and {NT_ 0}"
    return text


@pytest.fixture
def case_variant_placeholder_text():
    """大小写变体占位符"""
    # {nt_0} 而非 {NT_0}
    text = "Lowercase {nt_0} instead of {NT_0}"
    return text


# ══════════════════════════════════════════════════════════════════
# 超大内容 fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def single_long_paragraph():
    """单个超长段落（不含标题，无法按章节切分）"""
    sentence = "This is a technical document sentence with Kubernetes and Docker terminology. "
    return sentence * 200  # ~2800 词


@pytest.fixture
def super_long_code_block():
    """超长代码块"""
    lines = []
    for i in range(500):
        lines.append(f'echo "line {i:04d} with some text to make it longer"')
    return "```bash\n" + "\n".join(lines) + "\n```"


@pytest.fixture
def huge_table_md():
    """仅包含一个巨大表格的文档"""
    rows = ["| id | name | description | status | version |"]
    rows.append("|-----|------|-------------|--------|---------|")
    for i in range(100):
        rows.append(f"| {i:04d} | service-{i:04d} | A very long description for row {i} | active | v{i}.0.0 |")
    return "\n".join(rows)


# ══════════════════════════════════════════════════════════════════
# 资源异常 fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def missing_image_reference():
    """引用不存在的图片路径"""
    return "![missing image](assets/nonexistent_img.png)"


@pytest.fixture
def same_name_assets():
    """两个 session 使用同名资源文件"""
    from TransAgent.interface import DocumentAsset
    a1 = DocumentAsset(asset_id="a1", path="assets/img_01.png",
                       media_type="image/png", source_block_id="b1")
    a2 = DocumentAsset(asset_id="a2", path="assets/img_01.png",
                       media_type="image/png", source_block_id="b2")
    return a1, a2
