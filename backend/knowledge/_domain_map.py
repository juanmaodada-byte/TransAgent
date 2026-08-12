"""策略书领域标签 → 知识库封闭词表(10 项)归一化。

知识库 metadata.domain 用封闭词表(kubernetes/docker/cloud/network/security/
devops/database/data_ml/os/web,见 TASKS.md 成员 C 12.3);A 端策略书产出自由
标签(strategy_formulation/skill.md §分析维度,15+ 值)。新包 search_rag 的领域
过滤是精确匹配($or 仅兜底空域),必须先把策略标签归一到封闭词表;未映射标签回退
""——命中全局通用术语、不丢召回,领域专属词条暂缓(后续按需补映射)。
"""
_DOMAIN_MAP = {
    "Kubernetes/云原生": "kubernetes",
    "Docker/容器": "docker",
    "CI/CD": "devops",
    "DevOps": "devops",
    "网络安全": "security",
    "数据科学/ML": "data_ml",
    "数据库": "database",
    "网络/协议": "network",
    "前端开发": "web",
    "移动开发": "web",
    "监控/可观测性": "devops",
    "微服务": "cloud",
    # 未归一到封闭词表 → 通用兜底("" 命中全局术语)
    "分布式系统": "",
    "编程语言": "",
    "IoT": "",
    "其他": "",
}


# 知识库封闭词表(10 项,TASKS.md 成员 C 12.3)——已是这些值则幂等透传
_CLOSED_VOCAB = {
    "kubernetes", "docker", "cloud", "network", "security",
    "devops", "database", "data_ml", "os", "web",
}


def normalize_domain(label: str) -> str:
    """策略标签 → 封闭词表;空/未知回退 ""(命中全局通用术语)。

    幂等:输入已是封闭词表值(如 "network")则原样返回,不因映射表里没有就清空——
    否则适配层写入时会把已归一化的领域覆盖成 "",丢失领域过滤能力。
    """
    key = (label or "").strip()
    if not key:
        return ""
    if key.lower() in _CLOSED_VOCAB:
        return key.lower()
    return _DOMAIN_MAP.get(key, "")
