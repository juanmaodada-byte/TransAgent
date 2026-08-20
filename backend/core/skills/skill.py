"""
Skill 技能框架（目录化）
======================
Vibe Coder A | v1.0 | 2026-08-10 (D5)

每个技能 = 一个目录（skill.md + reference/ + scripts/）：

    core/skills/<skill_dir>/
      ├── skill.md        技能说明书（frontmatter: name/description + 主体内容）
      ├── reference/      参考材料目录（预留·可放术语示例库/白名单等）
      └── scripts/        实现脚本（Skill子类·execute）

核心不变式：
  1. system_prompt 运行时从 skill.md 加载（模块级缓存）——说明书不是py字符串常量
  2. 1技能 = 1目录 = 1说明书(skill.md) = 1次LLM调用
  3. name/description 类属性与 skill.md frontmatter 对应（登记处以类属性为准）
  4. 与agent解耦：agent说明书经构造参数注入（agent_prompt）→ 无循环导入
  5. 调用约定：系统提示词 = agent说明书 + skill.md主体内容（追加式）

使用示例：
    @register_skill
    class MySkill(Skill):
        name = "my_skill"
        description = "..."
        skill_dir = "my_skill"      # 对应 core/skills/my_skill/skill.md
        temperature = 0.3
        max_tokens = 4000
        json_mode = True

        async def execute(self, **inputs) -> Any:
            result = await chat(self.full_system_prompt(), user_message, ...)
            return parsed_result
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional


SKILLS_ROOT = Path(__file__).parent          # core/skills/
_MD_CACHE: dict[str, str] = {}               # skill_dir → skill.md主体内容（模块级缓存）


def _load_skill_md(skill_dir: str) -> str:
    """加载 {skills}/{skill_dir}/skill.md 的主体内容（剥离frontmatter·缓存）。"""
    if skill_dir in _MD_CACHE:
        return _MD_CACHE[skill_dir]
    path = SKILLS_ROOT / skill_dir / "skill.md"
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].lstrip("\n")
    _MD_CACHE[skill_dir] = text
    return text


class Skill(ABC):
    """
    技能基类。强制封装结构（每个技能必须提供）：
      - name           技能名（登记处索引·小写下划线式）
      - description    给人类看的说明
      - skill_dir      技能目录名（相对 core/skills/·默认取 name）
      - temperature / max_tokens / json_mode   调用参数
      - execute()      执行入口：组装用户消息 → 调用LLM → 解析/降级为结构化结果

    说明书（system_prompt）自动从 {skills}/{skill_dir}/skill.md 加载。

    与agent解耦：agent说明书通过 __init__ 注入（agent_prompt），
    技能本身不知道也不关心agent是谁。

    D6新增（共享池）：
      - requires: 本技能从池子拿取的 artifact 名（set）
      - provides: 本技能放回池子的 artifact 名（set）
      - validate_pool(pool)：执行前由agent工作流调用，缺数据即抛错
      - mark_pool_provided(pool)：执行成功后登记产出
    """
    name: str = ""
    description: str = ""
    skill_dir: str = ""            # 技能目录名（默认=name）
    temperature: float = 0.3
    max_tokens: int = 4000
    json_mode: bool = False
    requires: set = set()          # D6：从共享池拿取的 artifact 名
    provides: set = set()          # D6：放回共享池的 artifact 名

    def __init__(self, agent_prompt: str = ""):
        self.agent_prompt = agent_prompt   # agent说明书（由工作流注入·可空）

    @property
    def system_prompt(self) -> str:
        """技能说明书：skill.md 主体内容（运行时加载·缓存）。"""
        return _load_skill_md(self.skill_dir or self.name)

    def full_system_prompt(self) -> str:
        """组装调用提示词：agent说明书 + 技能说明书（追加式）。"""
        if self.agent_prompt:
            return f"{self.agent_prompt}\n\n{self.system_prompt}"
        return self.system_prompt

    def validate_pool(self, pool) -> None:
        """
        执行前校验共享池：requires 声明的数据必须齐备，缺则抛错拦住。
        由 agent 工作流在 execute 前调用；同时登记 consumers 审计。
        """
        missing = pool.check_missing(self.requires)
        if missing:
            raise RuntimeError(
                f"[Skill:{self.name}] 共享池缺数据: {missing} "
                f"(requires={sorted(self.requires)})"
            )
        pool.mark_consumed(self.requires, agent=self.name)

    def mark_pool_provided(self, pool) -> None:
        """执行成功后登记 provides 到池子（providers 审计）。"""
        pool.mark_provided(self.provides, agent=self.name)

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行技能。"""
        ...


class SkillRegistry:
    """全局技能登记处（与AgentRegistry同款模式）。"""

    _skills: dict[str, type[Skill]] = {}

    @classmethod
    def register(cls, skill_cls: type[Skill]) -> type[Skill]:
        """装饰器：注册一个技能类"""
        cls._skills[skill_cls.__name__] = skill_cls
        return skill_cls

    @classmethod
    def get(cls, name: str) -> Optional[type[Skill]]:
        """按类名查找技能类"""
        return cls._skills.get(name)

    @classmethod
    def list_all(cls) -> list[str]:
        """列出所有已注册技能名称"""
        return list(cls._skills.keys())

    @classmethod
    def clear(cls) -> None:
        """清空注册（测试用）"""
        cls._skills.clear()


def register_skill(cls: type[Skill]) -> type[Skill]:
    """装饰器语法糖：@register_skill"""
    return SkillRegistry.register(cls)
