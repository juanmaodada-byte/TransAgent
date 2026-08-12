"""
技能一：策略制定（译前）·实现脚本
=================================
Vibe Coder A | v1.2 | 2026-08-12 (D5·输出规范化)

说明书：本目录上级的 skill.md（由Skill基类运行时加载）。
本文件只放实现：组装用户消息 → 调用LLM → 校验/重试 → 解析为StrategyBook。

输入：用户偏好 + 翻译方向 + 文档抽样（前3000字）
输出：StrategyBook（翻译策略书·含ICT子领域标签·direction字段由本技能从输入记录）

v1.2 输出规范化（稳定输出契约）：
  - 强制字段完整性：LLM输出经 _normalize_strategy 校验，缺/非法字段 → 带纠正信息
    重试一次 → 仍缺才用默认值兜底并醒目告警（不再静默吞默认值）
  - 枚举字段归一化：ict_domain/difficulty/style/domain_confidence 收进封闭词表，
    rules 键补全，literal_ratio 钳制到 0~1

依赖：Skill框架（skills/skill.py）+ interface + llm_client.chat
不依赖：其他技能、译前agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

from transagent.interface import (
    UserPrefs, StrategyBook,
)
from transagent.backend.core.llm_client import chat
from transagent.backend.core.skills.skill import Skill, register_skill


# 策略书输出契约：LLM必填字段（direction 由系统从输入记录·不计入LLM输出）
STRATEGY_FIELDS = (
    "ict_domain", "domain_confidence", "difficulty", "style",
    "literal_ratio", "target_audience", "rules", "analysis_notes",
)
ICT_DOMAINS = {
    "Kubernetes/云原生", "Docker/容器", "CI/CD", "DevOps", "网络安全",
    "数据科学/ML", "数据库", "编程语言", "分布式系统", "微服务",
    "监控/可观测性", "网络/协议", "前端开发", "移动开发", "IoT", "其他",
}
DOMAIN_CONFIDENCES = {"high", "medium", "low"}
DIFFICULTIES = {"easy", "medium", "hard"}
STYLES = {"technical", "tutorial", "academic"}
RULES_KEYS = ("code", "tone", "sentence_length", "voice")


def _normalize_strategy(result: dict) -> tuple[list[str], dict]:
    """
    策略书字段校验 + 归一化。

    Returns:
        (missing, normalized)：missing 为缺/非法的字段名列表（空=完整）；
        normalized 为归一化后的字段值（重试仍失败时的兜底默认值已填入）。
    """
    raw = result or {}
    missing: list[str] = []

    ict_domain = str(raw.get("ict_domain", "")).strip()
    if ict_domain not in ICT_DOMAINS:
        missing.append("ict_domain")
    domain_conf = str(raw.get("domain_confidence", "")).strip()
    if domain_conf not in DOMAIN_CONFIDENCES:
        missing.append("domain_confidence")
    difficulty = str(raw.get("difficulty", "")).strip()
    if difficulty not in DIFFICULTIES:
        missing.append("difficulty")
    style = str(raw.get("style", "")).strip()
    if style not in STYLES:
        missing.append("style")
    try:
        literal_ratio = float(raw.get("literal_ratio"))
        if not (0.0 <= literal_ratio <= 1.0):
            raise ValueError
    except (TypeError, ValueError):
        literal_ratio = 0.6
        missing.append("literal_ratio")
    target_audience = str(raw.get("target_audience", "")).strip()
    if not target_audience:
        missing.append("target_audience")
    rules = raw.get("rules")
    if not isinstance(rules, dict) or not rules:
        missing.append("rules")
    analysis_notes = str(raw.get("analysis_notes", "")).strip()
    if not analysis_notes:
        missing.append("analysis_notes")

    # 兜底默认值（重试仍失败时使用）
    normalized = {
        "ict_domain": ict_domain if ict_domain in ICT_DOMAINS else "其他",
        "domain_confidence": domain_conf if domain_conf in DOMAIN_CONFIDENCES else "medium",
        "difficulty": difficulty if difficulty in DIFFICULTIES else "medium",
        "style": style if style in STYLES else "technical",
        "literal_ratio": literal_ratio,
        "target_audience": target_audience or "开发者",
        "rules": _normalize_rules(rules),
        "analysis_notes": analysis_notes,
    }
    return missing, normalized


def _normalize_rules(rules) -> dict:
    """rules 键补全；code 固定为 "notranslate"（不可被LLM改写）。"""
    base = {"code": "notranslate", "tone": "professional",
            "sentence_length": "medium", "voice": "active"}
    if isinstance(rules, dict):
        base.update({k: v for k, v in rules.items() if k in RULES_KEYS})
    base["code"] = "notranslate"
    return base


@register_skill
class StrategySkill(Skill):
    """技能一：策略制定。输入用户偏好+翻译方向+文档抽样 → 输出策略书。"""
    name = "strategy_formulation"
    description = "分析文档领域/难度/风格，产出翻译策略书（含ICT子领域标签与翻译方向）"
    skill_dir = "pre_skills/strategy_formulation"
    temperature = 0.3
    max_tokens = 2000
    json_mode = True

    async def execute(
        self,
        md_text: str,
        user_prefs: UserPrefs,
        direction: str = "en_to_zh",
    ) -> StrategyBook:
        """执行策略制定。

        Args:
            md_text: 文档全文（本技能只取前3000字抽样）
            direction: 翻译方向（由工作流给出·auto检测或用户指定）——
                       记录进策略书的 direction 字段，供译中/译后以该字段路由方向
        """
        user_message = f"""用户偏好：
- 默认风格：{user_prefs.default_style}
- 常用领域：{', '.join(user_prefs.domain_tags) if user_prefs.domain_tags else '无'}
- 直译/意译比例倾向：{user_prefs.literal_ratio}

翻译方向：{'中文 → 英文' if direction == 'zh_to_en' else '英文 → 中文'}

待分析文档（前3000字）：
{md_text[:3000]}"""
        try:
            result = await chat(
                self.full_system_prompt(), user_message,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                json_mode=self.json_mode,
            )
            if isinstance(result, dict):
                missing, normalized = _normalize_strategy(result)
                if missing:
                    # 字段不完整 → 带纠正信息重试一次
                    print(f"[StrategySkill] LLM输出缺/非法字段: {missing}（重试纠正一次）")
                    retry = await chat(
                        self.full_system_prompt(),
                        user_message + (
                            "\n\n【纠正】你上一次输出不完整。必须完整输出以下全部字段，缺一不可：\n"
                            + "、".join(STRATEGY_FIELDS)
                            + f"\n本次缺少/非法的字段：{missing}\n"
                            + "请重新输出一份完整的策略书JSON（枚举字段用给定枚举值）。"
                        ),
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                        json_mode=self.json_mode,
                    )
                    if isinstance(retry, dict):
                        missing, normalized = _normalize_strategy(retry)
                        if missing:
                            print(f"[StrategySkill] 重试后仍缺字段: {missing}（使用默认值兜底）")
                return StrategyBook(
                    ict_domain=normalized["ict_domain"],
                    domain_confidence=normalized["domain_confidence"],
                    difficulty=normalized["difficulty"],
                    style=normalized["style"],
                    literal_ratio=normalized["literal_ratio"],
                    target_audience=normalized["target_audience"],
                    rules=normalized["rules"],
                    analysis_notes=normalized["analysis_notes"],
                    direction=direction,   # 翻译方向由本技能从输入记录
                )
        except Exception as e:
            print(f"[StrategySkill] 策略制定失败，使用默认策略: {e}")
        return StrategyBook(direction=direction)  # 降级：默认策略（保留方向）
