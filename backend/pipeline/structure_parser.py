"""
结构解析器
==========
Vibe Coder B | v1.0 | 2026-08-06

职责：在LLM调用之前，用确定性正则+ICT白名单字典，
      识别不可译区域并注入占位符保护。
      毫秒级·零成本·100%确定性。

输入：MD结构化文本（纯字符串）
输出：受保护MD文本 + PlaceholderMap（两类占位符映射表）

两类占位符：
  {NT_n} — Non-Translatable — 代码/URL/命令/版本号 → 原样保留 → 译后还原原文
  {T_n}  — Translatable     — Mermaid标签/图片alt/SVG文字 → 正常翻译 → 译后还原译文

使用：
    from transagent.interface import PlaceholderMap
    from transagent.backend.pipeline.structure_parser import parse_structure
    protected_md, pmap = parse_structure(md_text)
"""

import re
from transagent.interface import PlaceholderMap, TermEntry


# ── ICT 白名单字典 ──────────────────────────────────────────────────
# 高频命令/API名/工具名 —— 确定性字符串匹配
# 来源：K8s文档、Docker文档、Git文档、Linux基础命令
# 持续更新中 —— 当前 v1.0 覆盖 ~100 条

ICT_WHITELIST: set[str] = {
    # ── K8s ──
    "kubectl", "kubeadm", "kubelet", "minikube", "kind",
    "etcd", "containerd", "cri-o", "coredns", "calico",
    "flannel", "istio", "linkerd", "helm", "helmfile",
    "kustomize", "kompose", "skaffold", "tilt", "telepresence",

    # ── Docker ──
    "docker", "docker-compose", "dockerfile", "docker swarm",
    "docker buildx", "docker scan", "containerd",

    # ── Git ──
    "git", "git clone", "git commit", "git push", "git pull",
    "git merge", "git rebase", "git stash", "git cherry-pick",
    "git bisect", "git diff", "git log", "git status",

    # ── Linux 基础 ──
    "systemctl", "journalctl", "sshd", "nginx", "apache2",
    "iptables", "cron", "rsyslog", "supervisor", "pm2",

    # ── 云平台 CLI ──
    "aws", "awscli", "gcloud", "az", "terraform", "ansible",
    "packer", "vagrant", "pulumi", "crossplane",

    # ── CI/CD ──
    "jenkins", "gitlab-ci", "github actions", "argocd", "fluxcd",
    "tekton", "spinnaker", "drone", "circleci", "travis-ci",

    # ── 监控/Observability ──
    "prometheus", "grafana", "alertmanager", "thanos", "loki",
    "opentelemetry", "jaeger", "zipkin", "datadog", "newrelic",
    "sentry", "elasticsearch", "kibana", "logstash",

    # ── 编程语言/Runtime ──
    "node.js", "python", "golang", "rust", "java", "typescript",
    "javascript", "ruby", "php", "swift", "kotlin",
    "pip", "npm", "yarn", "pnpm", "cargo", "go mod",
    "maven", "gradle", "brew", "choco", "snap",

    # ── 协议/API相关 ──
    "rest", "graphql", "grpc", "websocket", "http/2", "http/3",
    "tcp", "udp", "dns", "dhcp", "tls", "ssl", "ssh",
    "oauth2", "openid", "jwt", "saml", "ldap",

    # ── 数据库 ──
    "mysql", "postgresql", "mongodb", "redis", "etcd",
    "sqlite", "mariadb", "clickhouse", "cassandra", "neo4j",
}


def parse_structure(md_text: str) -> tuple[str, PlaceholderMap]:
    """
    识别不可译区域并注入占位符。

    Args:
        md_text: 输入的MD结构化文本

    Returns:
        protected_md: 受保护MD文本（{NT_n}和{T_n}占位符已注入）
        pmap: 占位符映射表
    """
    pmap = PlaceholderMap()
    nt_counter = 0
    t_counter = 0
    result = md_text

    # ── 第1步：围栏代码块 → {NT_n} ──
    result, nt_counter = _protect_fenced_code_blocks(result, nt_counter, pmap)

    # ── 第2步：行内代码 → {NT_n} ──
    result, nt_counter = _protect_inline_code(result, nt_counter, pmap)

    # ── 第3步：URL → {NT_n} ──
    result, nt_counter = _protect_urls(result, nt_counter, pmap)

    # ── 第4步：版本号 → {NT_n} ──
    result, nt_counter = _protect_versions(result, nt_counter, pmap)

    # ── 第5步：文件路径 → {NT_n} ──
    result, nt_counter = _protect_paths(result, nt_counter, pmap)

    # ── 第6步：邮箱 → {NT_n} ──
    result, nt_counter = _protect_emails(result, nt_counter, pmap)

    # ── 第7步：ICT白名单词汇 → {NT_n} ──
    result, nt_counter = _protect_whitelist(result, nt_counter, pmap)

    # ── 第8步：Mermaid标签 → {T_n} ──
    result, t_counter = _protect_mermaid_labels(result, t_counter, pmap)

    # ── 第9步：图片alt文本 → {T_n} ──
    result, t_counter = _protect_image_alt(result, t_counter, pmap)

    # ── 第10步：命令行（$ 或 > 开头）→ {NT_n} ──
    result, nt_counter = _protect_command_lines(result, nt_counter, pmap)

    pmap.nt_count = nt_counter
    pmap.t_count = t_counter
    return result, pmap


# ── 各项保护的内部实现 ──────────────────────────────────────────────

def _protect_fenced_code_blocks(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护 ``` ``` 围栏代码块"""
    pattern = re.compile(r'```[\s\S]*?```', re.MULTILINE)
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return f"\n{key}\n"
    return pattern.sub(_replace, text), counter


def _protect_inline_code(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护行内代码 `...`"""
    pattern = re.compile(r'`([^`]+)`')
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter


def _protect_urls(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护URL"""
    pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter


def _protect_versions(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护版本号 v1.2.3 格式"""
    pattern = re.compile(r'\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?\b')
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter


def _protect_paths(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护文件路径"""
    pattern = re.compile(r'(?:~?/[\w./-]+)+(?:\.[\w]+)?')
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter


def _protect_emails(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护邮箱地址"""
    pattern = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(0)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter


def _protect_whitelist(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护ICT白名单词汇（在非代码区域中出现的命令/API名）"""
    # 对白名单中的词汇做精确匹配（单词边界）
    for word in sorted(ICT_WHITELIST, key=len, reverse=True):  # 长的先匹配
        pattern = re.compile(rf'\b{re.escape(word)}\b')
        if pattern.search(text):
            key = f"{{NT_{counter}}}"
            pmap.nt_map[key] = word
            text = pattern.sub(key, text)
            counter += 1
    return text, counter


def _protect_mermaid_labels(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护Mermaid图中的标签文字（[...]、{...}、|...| 中的文字）"""
    # 只在 ```mermaid 代码块内处理
    mermaid_pattern = re.compile(r'```mermaid\n([\s\S]*?)```', re.MULTILINE)
    def _process_mermaid(m):
        content = m.group(1)
        # 匹配方括号标签 [文字]
        content = re.sub(r'\[([^\[\]{}"]+?)\]', lambda sm: _t_replace(sm, 1), content)
        # 匹配花括号标签 {文字}
        content = re.sub(r'\{([^{}"\n]+?)\}', lambda sm: _t_replace(sm, 1), content)
        # 匹配管道标签 |文字|
        content = re.sub(r'\|([^|"\n]+?)\|', lambda sm: _t_replace(sm, 1), content)
        return '```mermaid\n' + content + '```'

    def _t_replace(match, group_idx):
        nonlocal counter
        original = match.group(group_idx)
        if original.strip():
            key = f"{{T_{counter}}}"
            pmap.t_map[key] = original.strip()
            counter += 1
            return match.group(0).replace(original, key)
        return match.group(0)

    return mermaid_pattern.sub(_process_mermaid, text), counter


def _protect_image_alt(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护图片alt文本"""
    pattern = re.compile(r'!\[([^\]]*?)\]\(([^)]+)\)')
    def _replace(m):
        nonlocal counter
        alt_text = m.group(1)
        img_path = m.group(2)
        if alt_text.strip():
            key = f"{{T_{counter}}}"
            pmap.t_map[key] = alt_text.strip()
            counter += 1
            return f'![{key}]({img_path})'
        return m.group(0)
    return pattern.sub(_replace, text), counter


def _protect_command_lines(text: str, counter: int, pmap: PlaceholderMap) -> tuple[str, int]:
    """保护命令行（$ 或 > 开头）"""
    pattern = re.compile(r'^(\s*[\$>]\s+.+)$', re.MULTILINE)
    def _replace(m):
        nonlocal counter
        key = f"{{NT_{counter}}}"
        pmap.nt_map[key] = m.group(1)
        counter += 1
        return key
    return pattern.sub(_replace, text), counter
