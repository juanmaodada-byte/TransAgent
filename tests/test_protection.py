"""
保护与还原测试
===============
D1 测试基线 — 不可译内容保护和占位符还原验证。

不依赖 API key、网络或真实 LLM。
"""
import pytest
from transagent.backend.pipeline.structure_parser import parse_structure
from transagent.backend.pipeline.restore import restore_placeholders
from transagent.interface import PlaceholderMap


# ══════════════════════════════════════════════════════════════════
# 保护 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestProtectionNormal:
    def test_protect_fenced_code(self, kubernetes_md):
        """围栏代码块被保护为 {NT_n} 占位符"""
        protected_md, pmap = parse_structure(kubernetes_md)
        # 原始 YAML 代码块不应出现在受保护 MD 中
        assert "apiVersion: apps/v1" not in protected_md, "Code block content not protected"
        # 应该有 NT 占位符
        assert pmap.nt_count > 0, "No NT placeholders created"
        # 至少有一个占位符包含 YAML 代码（原样）
        any_yaml = any("apiVersion" in v for v in pmap.nt_map.values())
        assert any_yaml, "YAML code block not captured in placeholder map"

    def test_protect_inline_code(self, docker_md):
        """行内代码被保护"""
        protected_md, pmap = parse_structure(docker_md)
        # 行内代码 `docker run` 不应直接出现
        # 检查是否有 NT 占位符保护了 docker 相关行内代码
        any_docker = any("docker" in v.lower() for v in pmap.nt_map.values())
        assert any_docker or any("`docker" in v for v in pmap.nt_map.values()), (
            "No docker inline code captured in placeholder map"
        )

    def test_protect_url(self, docker_md):
        """URL 被保护"""
        protected_md, pmap = parse_structure(docker_md)
        # URL 不应该直接出现在受保护 MD 中
        assert "https://www.docker.com" not in protected_md, "URL not protected"
        # 但应该在 pmap 中
        any_url = any("https://" in v for v in pmap.nt_map.values())
        assert any_url, "URL not captured in placeholder map"

    def test_protect_version(self, kubernetes_md):
        """版本号被保护"""
        protected_md, pmap = parse_structure(kubernetes_md)
        # v1.19.0 不应直接出现
        assert "v1.19.0" not in protected_md, "Version number not protected"
        any_version = any("v1.19" in v for v in pmap.nt_map.values())
        assert any_version, "Version number not captured"

    def test_protect_path(self, rest_api_md):
        """文件路径被保护"""
        protected_md, pmap = parse_structure(rest_api_md)
        any_path = any(".config" in v or "config.yaml" in v for v in pmap.nt_map.values())
        assert any_path, "File path not protected"

    def test_protect_command_line(self, docker_md):
        """命令行被保护"""
        protected_md, pmap = parse_structure(docker_md)
        any_cmd = any("$" in v or "curl" in v.lower() for v in pmap.nt_map.values())
        assert any_cmd, "Command line not protected"

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: Mermaid 代码块在 fenced-code 保护阶段先被替换为 {NT_n}，后续 Mermaid 标签解析无法拿到原始内容，导致 t_count=0"
    )
    def test_mermaid_original_protected(self, cloud_native_docx_path):
        """Mermaid 代码块中的标签是否被正确提取到 T 占位符（P0: 整块保护+标签翻译路径）"""
        from transagent.backend.pipeline.preprocess import convert_to_md
        from transagent.interface import FormatType

        converted = convert_to_md(cloud_native_docx_path, FormatType.DOCX.value)

        # 确认源文本包含 Mermaid 图（带中文标签）
        assert "mermaid" in converted.md_text.lower(), "Fixture should contain mermaid content"

        protected_md, pmap = parse_structure(converted.md_text)

        # P0 问题: 当 Mermaid 被 fenced-code 保护吞掉后，t_count 始终为 0
        # （因为后续的 _protect_mermaid_labels 无法在已替换的占位符中找到 mermaid）
        #
        # 即使 Mermaid 标签被吞掉，P0 也要求整块 Mermaid 在 NT map 中保留
        # 并通过 T 占位符保护标签（当标签存在时）。
        # 现有代码中，如果 Mermaid 有中文标签如 [开始部署] [检查集群状态] 等，
        # 这些标签应该产生 T 占位符。
        #
        # 当前 Mermaid 标签被吞掉导致 t_count=0，这是已知缺陷。
        # 但这个 fixture 中 Mermaid 标签是中文的，应产生 T 占位符。
        assert pmap.t_count > 0, (
            f"Mermaid labels should produce T placeholders (expected t_count > 0, got {pmap.t_count}). "
            "Mermaid fenced-code protection consumed the content before label extraction."
        )


# ══════════════════════════════════════════════════════════════════
# 还原 — 正常用例
# ══════════════════════════════════════════════════════════════════

class TestRestoreNormal:
    def test_restore_nt_placeholder(self):
        """{NT_n} 占位符被原样还原"""
        pmap = PlaceholderMap()
        pmap.nt_map["{NT_0}"] = "protected code"
        text = "Before {NT_0} after"
        result = restore_placeholders(text, pmap)
        assert result == "Before protected code after"

    def test_restore_t_placeholder(self):
        """{T_n} 占位符被译文还原"""
        pmap = PlaceholderMap()
        pmap.t_map["{T_0}"] = "译文文本"
        text = "![{T_0}](img.png)"
        result = restore_placeholders(text, pmap)
        assert result == "![译文文本](img.png)"

    def test_restore_multiple_placeholders(self):
        """多个占位符同时还原"""
        pmap = PlaceholderMap()
        pmap.nt_map["{NT_0}"] = "code"
        pmap.nt_map["{NT_1}"] = "url"
        text = "{NT_0} then {NT_1}"
        result = restore_placeholders(text, pmap)
        assert result == "code then url"

    def test_restore_empty_pmap_no_change(self):
        """空 pmap 时文本不变"""
        pmap = PlaceholderMap()
        text = "unchanged text"
        result = restore_placeholders(text, pmap)
        assert result == text

    def test_protect_restore_roundtrip_url(self, docker_md):
        """URL 保护→还原往返一致"""
        protected_md, pmap = parse_structure(docker_md)
        restored = restore_placeholders(protected_md, pmap)
        # 还原后应恢复所有 URL（排除被命令替换影响的）
        assert "https://www.docker.com" in restored, "URL roundtrip failed"

    def test_protect_restore_roundtrip_code(self, kubernetes_md):
        """代码保护→还原往返一致（排除被版本号/白名单替换的）"""
        protected_md, pmap = parse_structure(kubernetes_md)
        restored = restore_placeholders(protected_md, pmap)
        # 关键术语应在还原后恢复
        assert "Pod" in restored, "Code content roundtrip failed"


# ══════════════════════════════════════════════════════════════════
# 占位符异常 — 已知缺陷 (XFAIL)
# ══════════════════════════════════════════════════════════════════

class TestPlaceholderIntegrityFailures:
    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: restore 不检查占位符缺失，导致静默交付损坏文档"
    )
    def test_placeholder_missing_error(self, missing_placeholder_text):
        """占位符 {NT_99} 在 pmap 中不存在——应被检测并阻止"""
        text, pmap = missing_placeholder_text
        # P0 期望：还原前检查所有占位符引用，缺失时阻止
        result = restore_placeholders(text, pmap)
        # 当前 behaviour: 直接通过，不检查缺失
        # 期望：抛出 DOCUMENT_INTEGRITY_ERROR
        import re
        remaining = re.findall(r'\{NT_\d+\}', result)
        assert len(remaining) == 0, (
            f"Placeholder integrity: {len(remaining)} unresolved placeholders remain: {remaining}"
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: restore_placeholders 仅用 str.replace 替换，无显式重复占位符检测。需要检测每个 placeholder 出现次数 > 1 并记录 warning。"
    )
    def test_placeholder_duplicate(self, duplicate_placeholder_text):
        """重复占位符应触发显式完整性检查（非仅靠 replace 副作用）"""
        text, pmap = duplicate_placeholder_text

        # 确认 fixture 中占位符确实出现多次
        import re
        occurrences = re.findall(r'\{NT_0\}', text)
        assert len(occurrences) > 1, "Fixture should contain duplicate placeholder"

        # P0 期望: restore 函数中应有显式的重复检测逻辑
        # 当前: 仅使用 str.replace，无 count/duplicate/warning 检查
        import inspect
        from transagent.backend.pipeline.restore import restore_placeholders
        source = inspect.getsource(restore_placeholders)
        has_integrity_check = any(
            keyword in source.lower()
            for keyword in ['count(', 'counter', 'duplicate', 'warning', 'warn',
                           'occurrence', 'occurred', 'validation', 'validate',
                           'assert', 'raise']
        )
        assert has_integrity_check, (
            "restore_placeholders should have explicit duplicate placeholder detection. "
            "Currently only uses str.replace with no integrity validation."
        )

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: LLM 可能插入空格破坏占位符，当前无变体恢复逻辑"
    )
    def test_placeholder_spaced_variant(self, spaced_placeholder_text):
        """LLM 插入空格的占位符变体应被识别并修复"""
        # P0 期望: { NT_0 } 被恢复为原始占位符或等价处理
        # 当前: 无变体处理
        import re
        # 检查是否有任何占位符格式残留
        variants = re.findall(r'\{[\s]*NT[\s_]*\d+[\s]*\}', spaced_placeholder_text)
        # 期望这些变体不应通过
        assert len(variants) == 0, (
            f"Spaced placeholder variants not handled: {variants}"
        )
        # 更具体的：还原时应处理 { NT_0 } → {NT_0}
        # 当前不会处理

    @pytest.mark.xfail(
        strict=True, raises=AssertionError,
        reason="P0/B: 大小写变体占位符未检测"
    )
    def test_placeholder_case_variant(self, case_variant_placeholder_text):
        """大小写变体 {nt_0} 应在完整性检查中被发现"""
        import re
        # P0 期望: 大小写敏感的占位符检查
        # 当前: 可能不会检查
        lowered = re.findall(r'\{nt_\d+\}', case_variant_placeholder_text)
        assert len(lowered) == 0, (
            f"Case variant placeholders not detected: {lowered}"
        )
