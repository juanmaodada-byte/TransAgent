"""
技能一：策略制定·评审草稿
========================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

1技能 = 1说明书(STRATEGY_SYSTEM_PROMPT) = 1次LLM调用 = 1个文件。
正式落地位置：transagent/backend/core/pre_skills/strategy_skill.py

依赖：Skill框架（draft_skill.py）+ interface + llm_client.chat
不依赖：技能二、译前agent模块（agent说明书由工作流在实例化时注入）→ 无循环导入
"""

from transagent.interface import (
    UserPrefs, StrategyBook,
)
from transagent.backend.core.llm_client import chat  # 真实调用（演示时被demo模块mock替换）
from transagent.tests.draft_skill import Skill, register_skill


STRATEGY_SYSTEM_PROMPT = """你正在执行【技能一：策略制定】——你是ICT领域资深技术翻译策略专家，任务是基于文档内容制定翻译策略。

注意：你看到的是文档开头部分（约前3000字符）的抽样，不是全文。
请基于抽样内容判断，如果信息不足以确定领域，domain_confidence 应下调为 medium 或 low。

## 分析维度

1. **ICT子领域识别**：识别文档所属的ICT子领域
   - Kubernetes/云原生、Docker/容器、CI/CD、DevOps
   - 网络安全、数据科学/ML、数据库、编程语言
   - 分布式系统、微服务、监控/可观测性、网络/协议
   - 前端开发、移动开发、IoT、其他

2. **难度评级**：
   - easy：纯技术文档（命令/配置为主），术语明确
   - medium：技术博客/教程，含解释性文字和少量企业文化用语
   - hard：技术白皮书/标准文档，含大量抽象概念

3. **风格判断**：
   - technical：严肃技术文档（API文档、配置指南）——准确优先，保留原文术语
   - tutorial：教程/博客（有亲和力，可略微口语化）
   - academic：学术/标准文档（严谨、正式）

4. **直译/意译比例**（0-1，1=全直译）：按文档类型给参考值
   - API文档/配置指南：0.8-0.9（专有名词/参数名保留，句式直译）
   - 技术博客/教程：0.5-0.6（解释性文字可意译，示例命令直译）
   - 白皮书/标准文档：0.4-0.5（抽象概念优先表达准确，允许意译长句）

5. **代码不译规则**：始终不翻译代码块、命令行、API名、文件路径
6. **目标读者**：开发者/运维/SRE/架构师

## 输出格式（JSON·严格遵守）

{
  "ict_domain": "Kubernetes/云原生",
  "domain_confidence": "high",
  "difficulty": "medium",
  "style": "technical",
  "literal_ratio": 0.6,
  "target_audience": "开发者",
  "rules": {
    "code": "notranslate",
    "tone": "professional",
    "sentence_length": "medium",
    "voice": "active"
  },
  "analysis_notes": "简要分析（1-2句，说明判断依据）"
}

字段约束：
- ict_domain 必须从上面的领域列表中选择，不明确时用"其他"
- difficulty/style/literal_ratio 必须与 analysis_notes 的判断一致
- rules.code 固定为 "notranslate"
"""


@register_skill
class StrategySkill(Skill):
    """技能一：策略制定。输入用户偏好+文档抽样 → 输出策略书。"""
    name = "strategy_formulation"
    description = "分析文档领域/难度/风格，产出翻译策略书"
    system_prompt = STRATEGY_SYSTEM_PROMPT
    temperature = 0.3
    max_tokens = 2000
    json_mode = True

    async def execute(self, md_text: str, user_prefs: UserPrefs) -> StrategyBook:
        user_message = f"""用户偏好：
- 默认风格：{user_prefs.default_style}
- 常用领域：{', '.join(user_prefs.domain_tags) if user_prefs.domain_tags else '无'}
- 直译/意译比例倾向：{user_prefs.literal_ratio}

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
                return StrategyBook(
                    ict_domain=result.get("ict_domain", ""),
                    domain_confidence=result.get("domain_confidence", "medium"),
                    difficulty=result.get("difficulty", "medium"),
                    style=result.get("style", "technical"),
                    literal_ratio=float(result.get("literal_ratio", 0.6)),
                    target_audience=result.get("target_audience", "开发者"),
                    rules=result.get("rules", {"code": "notranslate", "tone": "professional"}),
                )
        except Exception as e:
            print(f"[StrategySkill] 策略制定失败，使用默认策略: {e}")
        return StrategyBook()  # 降级：默认策略
