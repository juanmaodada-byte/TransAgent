"""
译前技能包·评审草稿
===================
Vibe Coder A | v1.0 | 2026-08-10 (D5 draft)

每个技能一个文件（1技能 = 1说明书 = 1次LLM调用 = 1个文件）：
  - strategy_skill.py   技能一：策略制定（StrategySkill）
  - term_skill.py       技能二：术语提取（TermExtractionSkill）

本包只做两件事：导入两个技能模块（触发技能注册）+ 统一再导出。
对外接口：`from transagent.tests.draft_pre_skills import StrategySkill, TermExtractionSkill`

正式落地位置：transagent/backend/core/pre_skills/（同结构）
"""

from transagent.tests.draft_pre_skills.strategy_skill import StrategySkill
from transagent.tests.draft_pre_skills.term_skill import TermExtractionSkill

__all__ = ["StrategySkill", "TermExtractionSkill"]
