# P0 覆盖矩阵

> D1 测试基线 | 2026-08-11 | 版本 v1.0

每个 P0 验收项至少映射到一个正常用例和至少一个失败用例（或明确标注 NOT_APPLICABLE）。

状态: PASS = 通过, XFAIL = strict 预期失败(已知缺陷), NOT_APPLICABLE = P0 不适用

---

## 6.1 输入与转换

| P0 验收项 | 正常用例 Fixture | 正常测试 ID | 失败用例 Fixture | 失败测试 ID | 状态 |
|---|---------------------|--------------------|-----------------------|--------------------|------|
| MD/TXT/DOCX 正确识别 | kubernetes.md, tech_whitepaper.md | test_detect_md, test_detect_txt | unsupported_ext | test_detect_unsupported_format | PASS |
| 未知格式明确拒绝 | kubernetes.md | test_detect_md | unsupported_ext | test_detect_unsupported_format | PASS |
| .doc 不冒充 DOCX | N/A (P0 不支持 .doc) | NOT_APPLICABLE | dot_doc_file | test_detect_doc_not_docx | XFAIL(P0/B) |
| 损坏文件和非法编码 | N/A | NOT_APPLICABLE | corrupt_docx_file, illegal_txt_encoding | test_corrupt_docx_fails, test_illegal_encoding_fails | XFAIL(P0/B) |
| DOCX 块顺序保留 100% | cloud_native_mixed.docx | test_docx_element_order | cloud_native_mixed.docx | test_docx_order_preserved | XFAIL(P0/B) |
| 表格单元格/文本保留 | cloud_native_mixed.docx | test_docx_table_cells | cloud_native_mixed.docx | test_docx_table_order | XFAIL(P0/B) |
| 图片数量/位置引用 | cloud_native_mixed.docx | test_docx_image_count | cloud_native_mixed.docx | test_docx_image_ref | XFAIL(P0/B) |
| conversion_warnings | cloud_native_mixed.docx | test_docx_conversion_warnings | N/A | NOT_APPLICABLE | PASS |

## 6.2 保护与还原

| P0 验收项 | 正常用例 Fixture | 正常测试 ID | 失败用例 Fixture | 失败测试 ID | 状态 |
|---|---------------------|--------------------|-----------------------|--------------------|------|
| 不可译内容损坏为 0 | kubernetes.md | test_protect_code_fenced | kubernetes.md | test_protect_code_integrity | XFAIL(P0/B) |
| URL 保护 | docker.md | test_protect_url | docker.md | test_protect_url_roundtrip | PASS |
| 命令保护 | docker.md | test_protect_command | docker.md | test_protect_command_roundtrip | PASS |
| 版本号保护 | rest_api.md | test_protect_version | rest_api.md | test_protect_version_roundtrip | PASS |
| 路径保护 | rest_api.md | test_protect_path | rest_api.md | test_protect_path_roundtrip | PASS |
| 占位符完整性 | docker.md | test_placeholder_values | missing_placeholder | test_placeholder_missing_error | XFAIL(P0/B) |
| 占位符缺失阻止导出 | N/A | NOT_APPLICABLE | missing_placeholder | test_placeholder_missing_error | XFAIL(P0/B) |
| 占位符重复 | N/A | NOT_APPLICABLE | duplicate_placeholder | test_placeholder_duplicate | XFAIL(P0/B) |
| 占位符空格变体 | N/A | NOT_APPLICABLE | spaced_placeholder | test_placeholder_spaced | XFAIL(P0/B) |
| 占位符大小写变体 | N/A | NOT_APPLICABLE | case_variant_placeholder | test_placeholder_case_variant | XFAIL(P0/B) |
| DOCUMENT_INTEGRITY_ERROR | N/A | NOT_APPLICABLE | missing_placeholder | test_document_integrity_error | XFAIL(P0/B) |
| Mermaid 还原一致性 | cloud_native_mixed.docx | test_mermaid_roundtrip | cloud_native_mixed.docx | test_mermaid_protection | XFAIL(P0/B) |

## 6.3 分块

| P0 验收项 | 正常用例 Fixture | 正常测试 ID | 失败用例 Fixture | 失败测试 ID | 状态 |
|---|---------------------|--------------------|-----------------------|--------------------|------|
| 内容覆盖且恰好一次 | tech_whitepaper.md | test_chunk_coverage | tech_whitepaper.md | test_chunk_block_coverage | XFAIL(P0/C) |
| 标题保留（长文） | tech_whitepaper.md | test_chunk_heading_preserved | tech_whitepaper.md | test_chunk_no_missing_headings | XFAIL(P0/C) |
| 单个超长段落 | single_long_paragraph | test_oversized_paragraph | single_long_paragraph | test_oversized_paragraph_token_limit | XFAIL(P0/C) |
| 超长代码块 | super_long_code_block | test_oversized_code_block | super_long_code_block | test_oversized_code_block_token | XFAIL(P0/C) |
| 巨大表格 | huge_table_md | test_huge_table | huge_table_md | test_huge_table_token | XFAIL(P0/C) |
| order 唯一稳定 | tech_whitepaper.md | test_chunk_order_stable | tech_whitepaper.md | test_chunk_order_consistent | PASS |
| tokenizer 上限验证 | N/A | test_token_limit_with_tokenizer | N/A | test_token_limit_skips_without_tokenizer | PASS |

## 6.4 导出

| P0 验收项 | 正常用例 Fixture | 正常测试 ID | 失败用例 Fixture | 失败测试 ID | 状态 |
|---|---------------------|--------------------|-----------------------|--------------------|------|
| 标题重建 | cloud_native_mixed.docx | test_export_docx_heading_rebuild | N/A | NOT_APPLICABLE | PASS |
| 列表重建 | cloud_native_mixed.docx | test_export_list_count | N/A | test_export_list_rebuild | XFAIL(P0/D) |
| 表格重建 | cloud_native_mixed.docx | test_export_table_cells | N/A | test_export_table_rebuild | XFAIL(P0/D) |
| 图片重建 | cloud_native_mixed.docx | test_export_image_count | N/A | test_export_image_rebuild | XFAIL(P0/D) |
| 代码区域重建 | cloud_native_mixed.docx | test_export_code_presence | N/A | test_export_code_rebuild | XFAIL(P0/D) |
| 文件隔离 | N/A | NOT_APPLICABLE | same_name_assets | test_export_session_isolation | XFAIL(P0/D) |
| DOCX 结构检查 | cloud_native_mixed.docx | test_export_docx_structure | N/A | test_export_docx_structure | XFAIL(P0/D) |

---

## 6.5 契约与消费者

| P0 验收项 | 正常用例 | 正常测试 ID | 失败用例 | 失败测试 ID | 状态 |
|---|------------------------|---------------------|-----------------------|--------------------|------|
| DocumentBlock 默认值独立 | N/A | test_document_block_defaults_independent | N/A | N/A | PASS |
| PreprocessResult 新增字段 | N/A | test_preprocess_new_fields | N/A | N/A | PASS |
| Chunk 新增字段 | N/A | test_chunk_new_fields | N/A | N/A | PASS |
| 关键字构造兼容 | N/A | test_old_caller_still_works | N/A | N/A | PASS |
| 共享可变默认值隔离 | N/A | test_defaults_independent | N/A | N/A | PASS |
| 序列化兼容 | N/A | test_placeholder_to_dict | N/A | N/A | PASS |

---

## XFAIL 台账

| # | 测试 ID | 用户风险 | 失败原因 | 归属阶段 |
|---|---------|---------|---------|---------|
| 1 | test_docx_order_preserved | DOCX 段落/表格顺序被破坏，翻译后文档语义改变 | DOCX parser 先读全部段落再追加全部表格 | B |
| 2 | test_docx_table_order | 表格出现在错误位置 | 同上 | B |
| 3 | test_docx_image_ref | 图片无位置引用，丢失图片上下文 | 图片提取后未在 MD 中插入引用 | B |
| 4 | test_detect_doc_not_docx | .doc 被当作 DOCX 处理，产生混乱 | 扩展名映射 .doc → DOCX | B |
| 5 | test_corrupt_docx_fails | 损坏文件无明确错误提示 | 缺少格式头验证 | B |
| 6 | test_mermaid_protection | Mermaid 图被 fenced-code 占位逻辑吞掉 | fenced code 先于 Mermaid 标签处理 | B |
| 7 | test_placeholder_missing_error | 占位符缺失时仍静默导出 | restore 不检查缺失 | B |
| 8 | test_placeholder_duplicate | 重复占位符未被检测 | restore 不检查重复 | B |
| 9 | test_placeholder_spaced | LLM 可插入空格破坏占位符 | 无变体恢复逻辑 | B |
| 10 | test_chunk_no_missing_headings | 长文分块后标题丢失 | heading 只存入 heading_path 不入正文 | C |
| 11 | test_oversized_paragraph_token_limit | 单个超长段落超过 token 上限 | 无段落内切分逻辑 | C |
| 12 | test_export_table_rebuild | DOCX 导出无法重建表格 | exporter 不处理管道表 | D |
| 13 | test_export_list_rebuild | DOCX 导出无法重建列表 | exporter 不处理列表项样式 | D |
| 14 | test_export_code_presence | DOCX 导出代码区域无等宽字体 | exporter 跳过代码块 | D |
| 15 | test_export_session_isolation | 并发 session 文件可能覆盖 | 导出路径固定为 output.docx | D |
