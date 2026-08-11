"""
Golden Fixture 生成器: cloud_native_mixed.docx
================================================
D1 测试基础设施 — 生成包含标题、段落、列表、表格、图片、代码和 Mermaid 的混合 DOCX。

用法:
    python tests/fixtures/generate_docx_fixture.py

产出: tests/fixtures/cloud_native_mixed.docx
"""
import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "cloud_native_mixed.docx")


def add_styled_heading(doc, text, level):
    h = doc.add_heading(text, level=level)
    return h


def add_code_block(doc, code_text, language=""):
    """Add a code block as a distinctly styled paragraph (DOCX has no native code block)."""
    p = doc.add_paragraph()
    run = p.add_run(code_text)
    run.font.name = "Courier New"
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x2D, 0x2D, 0x2D)
    return p


def add_list(doc, items, ordered=False):
    for i, item in enumerate(items):
        prefix = f"{i + 1}." if ordered else "•"
        p = doc.add_paragraph(f"{prefix} {item}")
    return p


def build_document():
    doc = Document()

    # ── 1. 标题 ──
    add_styled_heading(doc, "云原生应用部署指南", level=1)

    # ── 2. 段落 ──
    doc.add_paragraph(
        "本文档介绍如何在 Kubernetes 集群中部署一个典型的云原生应用。"
        "该应用包含 Web 前端、API 服务和 Redis 缓存层。"
    )

    # ── 3. 二级标题 ──
    add_styled_heading(doc, "1. 环境准备", level=2)

    doc.add_paragraph(
        "在开始部署之前，请确保以下工具已安装并正确配置："
    )

    # ── 4. 列表 ──
    add_list(doc, [
        "kubectl 命令行工具（版本 v1.28.0 或更高）",
        "Helm v3.12+ 包管理器",
        "Docker Desktop 或 Rancher Desktop",
        "访问集群的 kubeconfig 配置文件",
    ], ordered=False)

    # ── 5. 三级标题 + 代码 ──
    add_styled_heading(doc, "1.1 验证集群连接", level=3)

    doc.add_paragraph("运行以下命令验证集群连接状态：")

    add_code_block(doc, "$ kubectl cluster-info\n$ kubectl get nodes")

    # ── 6. 表格 ──
    add_styled_heading(doc, "2. 资源配置表", level=2)

    doc.add_paragraph("各服务的资源需求如下：")

    table = doc.add_table(rows=4, cols=4)
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    headers = ["服务", "CPU 请求", "内存请求", "副本数"]
    for i, text in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = text
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True

    data = [
        ["Web 前端", "100m", "128Mi", "2"],
        ["API 服务", "250m", "256Mi", "3"],
        ["Redis", "50m", "64Mi", "1"],
    ]
    for row_idx, row_data in enumerate(data):
        for col_idx, text in enumerate(row_data):
            table.rows[row_idx + 1].cells[col_idx].text = text

    # ── 7. 段落 ──
    doc.add_paragraph("")  # spacing
    doc.add_paragraph(
        "请根据实际负载情况调整上述资源配置。在高并发场景下，"
        "建议将 API 服务的副本数增加到 5 个或更多。"
    )

    # ── 8. 代码块（YAML） ──
    add_styled_heading(doc, "3. 部署清单示例", level=2)

    doc.add_paragraph("以下是一个完整的 Deployment 清单：")

    add_code_block(doc,
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: web-frontend\n"
        "  labels:\n"
        "    app: web\n"
        "    version: v2.1.0\n"
        "spec:\n"
        "  replicas: 2\n"
        "  selector:\n"
        "    matchLabels:\n"
        "      app: web\n"
        "  template:\n"
        "    metadata:\n"
        "      labels:\n"
        "        app: web\n"
        "    spec:\n"
        "      containers:\n"
        "      - name: nginx\n"
        "        image: nginx:1.25-alpine\n"
        "        ports:\n"
        "        - containerPort: 80\n"
    )

    # ── 9. 段落 ──
    doc.add_paragraph(
        "部署应用时需要特别注意镜像版本标签，避免使用 latest 标签。"
        "推荐使用语义化版本号，例如 nginx:1.25.3。"
    )

    # ── 10. 图片（占位·生成一个简单的测试像素） ──
    add_styled_heading(doc, "4. 架构示意图", level=2)

    # 生成一个最小 PNG 图片嵌入 DOCX
    # 1x1 蓝色像素 PNG
    png_data = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\x62\x60\x60\x00\x00\x00\x04\x00\x01\xa2\x17\xa5\x16'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    from io import BytesIO
    img_stream = BytesIO(png_data)
    doc.add_picture(img_stream, width=Inches(2))
    doc.add_paragraph("[图1] 系统架构示意图")

    # ── 11. Mermaid 图 ──
    add_styled_heading(doc, "5. 部署流程", level=2)

    doc.add_paragraph("部署流程如下所示（Mermaid 流程图）：")

    add_code_block(doc,
        "```mermaid\n"
        "graph TD\n"
        "    A[开始部署] --> B{检查集群状态}\n"
        "    B -->|正常| C[应用配置]\n"
        "    B -->|异常| D[修复集群]\n"
        "    C --> E[启动Pod]\n"
        "    E --> F{健康检查}\n"
        "    F -->|通过| G[部署完成]\n"
        "    F -->|失败| H[回滚]\n"
        "```"
    )

    # ── 12. 内联代码 + 链接 ──
    add_styled_heading(doc, "6. 验证与监控", level=2)

    doc.add_paragraph(
        "部署完成后，使用 kubectl get pods 查看 Pod 状态。"
        "更多信息请参考官方文档 https://kubernetes.io/docs/concepts/workloads/controllers/deployment/。"
    )

    doc.add_paragraph(
        "相关配置文件位于 /etc/kubernetes/manifests/ 目录下。"
        "如有问题请联系 devops@example.com。"
    )

    # ── 保存 ──
    doc.save(OUTPUT)
    print(f"Generated: {OUTPUT}")


if __name__ == "__main__":
    build_document()
