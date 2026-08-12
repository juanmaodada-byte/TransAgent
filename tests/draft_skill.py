"""
Skill框架·评审草稿
==================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

通用技能框架——与译前无关，译中/译后技能复用同一框架。
正式落地位置：transagent/backend/core/skill.py

核心不变式：1个技能 = 1份说明书(system_prompt) + 1次LLM调用 + 严格封装结构
（name / description / system_prompt / temperature / max_tokens / json_mode / execute）。

与agent的解耦方式：
  - agent说明书由agent模块持有，通过构造参数注入（agent_prompt），
    技能不反向依赖agent模块 → 无循环导入
  - 调用约定：系统提示词 = agent说明书 + 技能说明书（追加式）
"""

from typing import Any, Optional
from abc import ABC, abstractmethod


class Skill(ABC):
    """
    技能基类。强制封装结构（每个技能必须提供）：
      - name           技能名（登记处索引·小写下划线式）
      - description    给人类看的说明
      - system_prompt  技能说明书（详细指令·输出格式）
      - temperature    温度（技能独立的严格程度）
      - max_tokens     最大输出
      - json_mode      是否要求JSON输出
      - execute()      执行入口：组装用户消息 → 调用LLM → 解析/降级为结构化结果

    与agent解耦：agent说明书通过 __init__ 注入（agent_prompt），
    技能本身不知道也不关心agent是谁。
    """
    name: str = ""
    description: str = ""
    system_prompt: str = ""
    temperature: float = 0.3
    max_tokens: int = 4000
    json_mode: bool = False

    def __init__(self, agent_prompt: str = ""):
        self.agent_prompt = agent_prompt   # agent说明书（由工作流注入·可空）

    def full_system_prompt(self) -> str:
        """组装调用提示词：agent说明书 + 技能说明书（追加式）。"""
        if self.agent_prompt:
            return f"{self.agent_prompt}\n\n{self.system_prompt}"
        return self.system_prompt

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行技能。"""
        ...


class SkillRegistry:
    """技能登记处（评审版·正式版并入AgentRegistry同款模式）"""
    _skills: dict[str, type[Skill]] = {}

    @classmethod
    def register(cls, skill_cls: type[Skill]) -> type[Skill]:
        cls._skills[skill_cls.__name__] = skill_cls
        return skill_cls

    @classmethod
    def get(cls, name: str) -> Optional[type[Skill]]:
        return cls._skills.get(name)

    @classmethod
    def list_all(cls) -> list[str]:
        return list(cls._skills.keys())


def register_skill(cls: type[Skill]) -> type[Skill]:
    """装饰器语法糖：@register_skill"""
    return SkillRegistry.register(cls)
