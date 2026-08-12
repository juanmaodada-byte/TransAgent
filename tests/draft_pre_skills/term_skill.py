"""
技能二：术语提取·评审草稿
========================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

1技能 = 1说明书(TERM_SYSTEM_PROMPT) = 1次LLM调用 = 1个文件。
正式落地位置：transagent/backend/core/pre_skills/term_skill.py

依赖：Skill框架（draft_skill.py）+ interface + llm_client.chat
不依赖：技能一、译前agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入

本技能的解析助手（_to_entry/_dedup）只属于自己，不与其他技能共享——放在本文件内。
"""

from typing import Optional

from transagent.interface import (
    UserPrefs, StrategyBook, TermTable, TermEntry,
    Confidence, TermSource,
)
from transagent.backend.core.llm_client import chat  # 真实调用（演示时被demo模块mock替换）
from transagent.tests.draft_skill import Skill, register_skill


TERM_SYSTEM_PROMPT = """你正在执行【技能二：术语提取】——你是ICT领域资深术语专家，任务是从ICT文档中提取术语并确定目标语言译法。

依赖说明：翻译方向与ICT子领域标签由用户消息提供，术语消歧必须结合领域标签判断
（如"container"在K8s→"容器"，在物流→"集装箱"）。

输入与分批说明（重要）：
- 系统会尽可能提供文档全文；文档过长时会分批提供，每次只给你文档的一个部分（chunk），
  当前为第几部分会在用户消息中注明。
- 你只需要扫描"本次提供的文本"，每次调用都按完整标准提取，不得降低标准。
- 同一术语在本部分内只输出一次（本部分内去重）。
- 同一术语若在多个部分出现，译法必须保持一致（以社区惯例/官方文档为准）。
- 系统会自动合并各部分的提取结果并去重（首次出现的译法优先保留），你无需顾虑其他部分。

## 一、翻译方向：英文 → 中文

### 提取标准

**要提取**（每个都必须是领域术语，不是普通英文单词）：
- 技术概念/机制：rolling update、service mesh、readiness probe、circuit breaker
- 产品/项目名：Kubernetes、Prometheus、Docker（action="notranslate"）
- 协议/规范：gRPC、HTTP/2、OAuth 2.0、OpenTelemetry
- 关键技术缩写：CI/CD、SRE、IaC（首次出现时译法给全称+缩写，如"持续集成/持续部署（CI/CD）"）
- 架构/模式术语：sidecar、blue-green deployment、event-driven

**不提取**：
- 普通英语单词（network、service、deployment 单独出现时——除非有特定领域译法差异）
- 已被占位符保护的 {NT_n}/{T_n}（那是代码/命令/URL）
- 完整句子、短语级表达
- 同一术语只出现一次（去重）

**数量**：通常10-30条。术语密集的文档可到40条，不要为了凑数提取普通词。

### ICT特殊规则（英文 → 中文）

- 技术缩写：首次出现给全称+缩写
- 中英混合术语：保留英文原词+中文释义（如"Kubernetes（K8s）集群"）
- 裸API名/配置项/命令：action="notranslate"（如kubectl、git clone、docker run、yaml中的字段名）
- 领域消歧："container"在K8s→"容器"，在物流→"集装箱"——必须结合给定领域标签判断

## 二、翻译方向：中文 → 英文

### 提取标准

**要提取**（中文术语 → 英文译法）：
- 中文技术概念：滚动更新→rolling update、就绪探针→readiness probe、负载均衡→load balancing
- 中文技术动作：部署、回滚、扩缩容、灰度发布
- 中文缩写/黑话：需要确定标准英文表达
- 数量：通常10-30条。不要为了凑数提取普通中文词。

**不提取 / 特殊处理**：
- 文中已嵌入的英文词（API key、OpenAI、stream、true 等）→ 这些已经是英文（目标语言），
  标记 action="notranslate"，translation 填原文，不需要给中文释义
- 产品/模型/API名（DeepSeek、Kubernetes、OpenAI API）→ notranslate
- 代码块、命令、参数名（已被{NT_n}占位符保护）
- 普通中文词语（"创建"、"之后"、"您"等非技术表达）

### ICT特殊规则（中文 → 英文）

- 中文术语给出标准英文表达（优先官方文档/社区惯例）
- 嵌入英文词/产品名/API名一律 notranslate 保留原文
- 领域消歧同样适用：结合给定领域标签判断

## 译法查证（按优先级·两个方向通用）

1. **RAG命中** → 复用库中历史译法（confidence: high）
2. **RAG未命中** → 依据社区惯例/官方文档译法（confidence: medium）
3. **不确定/无公认译法** → 自拟译法（confidence: low，放入 pending_terms）

## 置信度定义（两个方向通用）

- high：官方文档/术语表有明确定论的译法（如 rolling update→滚动更新、流式输出→streaming output）
- medium：社区普遍使用但无官方定论的译法
- low：你自己拟的、无把握的译法（如新兴术语、企业内部黑话）

## 输出格式（JSON·严格遵守）

{
  "term_table": [
    {
      "term": "rolling update",
      "translation": "滚动更新",
      "domain": "Kubernetes/云原生",
      "confidence": "high",
      "action": "translate",
      "source": "RAG命中"
    }
  ],
  "pending_terms": [
    {
      "term": "GitOps pipeline",
      "translation": "GitOps流水线",
      "domain": "CI/CD",
      "confidence": "low",
      "action": "translate",
      "source": "LLM生成"
    }
  ]
}

字段约束：
- 必须输出 term_table 和 pending_terms 两个数组（可为空数组）
- term/translation 不能为空字符串
- domain 必须与给定的ICT子领域一致
- action 只能是 "translate" 或 "notranslate"
- confidence 只能是 "high" | "medium" | "low"
"""


@register_skill
class TermExtractionSkill(Skill):
    """
    技能二：术语提取。输入 方向+领域标签+文本（全文或某个chunk）→ 输出单批术语表。

    单批语义：一次调用处理"本次提供的文本"。多chunk时由工作流多次调用本技能，
    再合并去重——本技能不感知其他批次，也不需要感知（合并是协调器的职责）。
    """
    name = "term_extraction"
    description = "从文本中提取术语并定译法，产出术语表（单批）"
    system_prompt = TERM_SYSTEM_PROMPT
    temperature = 0.2
    max_tokens = 4000
    json_mode = True

    async def execute(
        self,
        fragment: str,
        strategy: StrategyBook,
        direction: str,
        user_prefs: UserPrefs,
        part_label: str = "全文",
    ) -> TermTable:
        """执行一次术语提取。

        Args:
            fragment: 本次提供的文本（全文 或 单个chunk）
            part_label: 本次是第几部分（如"第2/3部分"），写入用户消息供LLM感知
        """
        direction_label = "中文 → 英文" if direction == "zh_to_en" else "英文 → 中文"
        user_message = f"""翻译方向：{direction_label}
ICT子领域：{strategy.ict_domain}
本次处理文本：{part_label}

待提取术语的文本：
{fragment}"""

        table = TermTable()
        try:
            result = await chat(
                self.full_system_prompt(), user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=self.json_mode,
            )
            if not isinstance(result, dict):
                return table

            for t in list(result.get("term_table", []) or []):
                entry = _to_entry(t, strategy.ict_domain, user_prefs.user_id)
                if entry:
                    table.entries.append(entry)
            for t in list(result.get("pending_terms", []) or []):
                entry = _to_entry(t, strategy.ict_domain, user_prefs.user_id, force_pending=True)
                if entry:
                    table.pending_entries.append(entry)

            # 批内去重（同一术语本批内只保留一条）
            table.entries = _dedup(table.entries)
            table.pending_entries = _dedup(table.pending_entries)
        except Exception as e:
            print(f"[TermExtractionSkill] 术语批次失败: {e}")

        return table


def _to_entry(t: dict, domain: str, user_id: str,
              force_pending: bool = False) -> Optional[TermEntry]:
    """LLM返回的原始dict → TermEntry（空术语过滤·低置信度分流）"""
    term_text = str(t.get("term", "")).strip()
    if not term_text:
        return None
    confidence = t.get("confidence", "medium")
    if force_pending:
        confidence = Confidence.LOW.value
    return TermEntry(
        term=term_text,
        translation=t.get("translation", ""),
        domain=domain,
        confidence=confidence,
        action=t.get("action", "translate"),
        source=TermSource.LLM_GEN.value,   # 诚实化：无RAG/搜索时统一LLM生成
        user_id=user_id,
    )


def _dedup(entries: list[TermEntry]) -> list[TermEntry]:
    """按term去重（保留第一条），忽略空term。"""
    seen: set[str] = set()
    deduped: list[TermEntry] = []
    for e in entries:
        if e.term and e.term not in seen:
            seen.add(e.term)
            deduped.append(e)
    return deduped
