"""
pytest conftest — D1 test infrastructure.
Provides golden fixture loaders and exception fixtures.
Ensures tests run without API keys, network, or real LLM calls.

Adapted for the merged TransAgent package: the standalone-handoff
"TransAgent -> transagent" importlib bridge is removed — in this package
``transagent`` imports directly from the package root.
"""
import os
import subprocess
import sys

# Make the package root importable when tests are run from this directory tree.
_pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)


# ══════════════════════════════════════════════════════════════════
# Golden Fixture Helpers
# ══════════════════════════════════════════════════════════════════

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")

FIXTURE_PATHS = {
    "kubernetes": os.path.join(FIXTURES_DIR, "kubernetes_deployment.md"),
    "docker": os.path.join(FIXTURES_DIR, "docker_tutorial.md"),
    "rest_api": os.path.join(FIXTURES_DIR, "rest_api.md"),
    "tech_whitepaper": os.path.join(FIXTURES_DIR, "tech_whitepaper.md"),
    "plain_technical_docx": os.path.join(FIXTURES_DIR, "plain_technical.docx"),
    "api_reference_docx": os.path.join(FIXTURES_DIR, "api_reference.docx"),
    "cloud_native_mixed": os.path.join(FIXTURES_DIR, "cloud_native_mixed.docx"),
    "okapi_probe_mixed": os.path.join(FIXTURES_DIR, "okapi_probe_mixed.docx"),
    "real_word_97_mixed": os.path.join(FIXTURES_DIR, "real_word_97_mixed.doc"),
}


def _ensure_generated_fixtures():
    required = [
        FIXTURE_PATHS["plain_technical_docx"],
        FIXTURE_PATHS["api_reference_docx"],
        FIXTURE_PATHS["okapi_probe_mixed"],
        os.path.join(FIXTURES_DIR, "format_exceptions", "minimal.pdf"),
        os.path.join(FIXTURES_DIR, "format_exceptions", "minimal_word.doc"),
        os.path.join(FIXTURES_DIR, "format_exceptions", "workbook_named_doc.doc"),
        os.path.join(FIXTURES_DIR, "format_exceptions", "encrypted_ole.doc"),
    ]
    if all(os.path.exists(p) for p in required):
        return
    subprocess.run(
        [sys.executable, os.path.join(FIXTURES_DIR, "generate_docx_fixture.py")],
        check=True,
        cwd=os.path.dirname(FIXTURES_DIR),
    )


_ensure_generated_fixtures()


def load_fixture_text(name: str) -> str:
    """Load a markdown golden fixture by name."""
    path = FIXTURE_PATHS.get(name)
    if path is None:
        raise ValueError(f"Unknown fixture: {name}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_fixture_docx_path(name: str = "cloud_native_mixed") -> str:
    """Get the absolute path to a DOCX golden fixture."""
    return FIXTURE_PATHS[name]


import pytest


@pytest.fixture(scope="module")
def kubernetes_md():
    return load_fixture_text("kubernetes")


@pytest.fixture(scope="module")
def docker_md():
    return load_fixture_text("docker")


@pytest.fixture(scope="module")
def rest_api_md():
    return load_fixture_text("rest_api")


@pytest.fixture(scope="module")
def tech_whitepaper_md():
    return load_fixture_text("tech_whitepaper")


@pytest.fixture(scope="module")
def cloud_native_docx_path():
    return load_fixture_docx_path("cloud_native_mixed")


@pytest.fixture(scope="module")
def okapi_probe_docx_path():
    return load_fixture_docx_path("okapi_probe_mixed")


@pytest.fixture(scope="module")
def real_word_97_doc_path():
    path = FIXTURE_PATHS["real_word_97_mixed"]
    if not os.path.exists(path):
        subprocess.run(
            [sys.executable, os.path.join(FIXTURES_DIR, "generate_docx_fixture.py")],
            check=True,
            cwd=os.path.dirname(FIXTURES_DIR),
        )
    if not os.path.exists(path):
        pytest.skip("real Word 97-2003 DOC fixture unavailable")
    return path


@pytest.fixture(scope="module")
def plain_technical_docx_path():
    return load_fixture_docx_path("plain_technical_docx")


@pytest.fixture(scope="module")
def api_reference_docx_path():
    return load_fixture_docx_path("api_reference_docx")


@pytest.fixture
def all_golden_md_fixtures():
    """Return dict of all markdown golden fixtures."""
    return {
        name: load_fixture_text(name)
        for name in ["kubernetes", "docker", "rest_api", "tech_whitepaper"]
    }


# ══════════════════════════════════════════════════════════════════
# 异常 fixtures — 格式异常
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
    """非法编码的 TXT 文件"""
    f = tmp_path / "bad_encoding.txt"
    f.write_bytes(b'\xff\xfe\x00\x00invalid utf8 bytes\x80\x81\x82')
    return str(f)


@pytest.fixture
def dot_doc_file(tmp_path):
    """.doc 文件（旧格式，非 DOCX）"""
    f = tmp_path / "legacy.doc"
    f.write_bytes(b'\xd0\xcf\x11\xe0' + b'\x00' * 100)
    return str(f)


# ══════════════════════════════════════════════════════════════════
# 异常 fixtures — 占位符异常
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def missing_placeholder_text():
    """包含不存在的占位符引用的文本"""
    from transagent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "original text"
    text = "Some text with {NT_0} and a missing {NT_99}"
    return text, pmap


@pytest.fixture
def duplicate_placeholder_text():
    """还原后仍有残留占位符的文本"""
    from transagent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "code block"
    text = "Here {NT_0} and here {NT_0} again"
    return text, pmap


@pytest.fixture
def unknown_placeholder_text():
    """包含未在 pmap 中定义的占位符"""
    from transagent.interface import PlaceholderMap
    pmap = PlaceholderMap()
    pmap.nt_map["{NT_0}"] = "known"
    text = "Known {NT_0} and unknown {NT_X}"
    return text, pmap


@pytest.fixture
def spaced_placeholder_text():
    """被 LLM 插入空格的占位符"""
    return "Broken { NT_0 } and { NT_0} and {NT_ 0}"


@pytest.fixture
def case_variant_placeholder_text():
    """大小写变体占位符"""
    return "Lowercase {nt_0} instead of {NT_0}"


# ══════════════════════════════════════════════════════════════════
# 异常 fixtures — 超大内容
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def single_long_paragraph():
    """单个超长段落（不含标题，无法按章节切分）"""
    sentence = "This is a technical document sentence with Kubernetes and Docker terminology. "
    return sentence * 200


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
# 异常 fixtures — 资源异常
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def missing_image_reference():
    """引用不存在的图片路径"""
    return "![missing image](assets/nonexistent_img.png)"


@pytest.fixture
def same_name_assets():
    """两个 session 使用同名资源文件"""
    from transagent.interface import DocumentAsset
    a1 = DocumentAsset(
        asset_id="a1", path="assets/img_01.png",
        media_type="image/png", source_block_id="b1")
    a2 = DocumentAsset(
        asset_id="a2", path="assets/img_01.png",
        media_type="image/png", source_block_id="b2")
    return a1, a2
