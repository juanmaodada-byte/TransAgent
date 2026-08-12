"""
TransAgent 翻译核心模块
=======================
Vibe Coder A | D2 | 2026-08-07

子模块：
  - agent_framework: Sub-Agent 调用框架（BaseAgent / spawn / spawn_parallel / AgentRegistry）
  - skills:          技能框架与技能目录（skill.py基类 + <skill_dir>/skill.md+reference+scripts）
  - llm_client:       DeepSeek V4 Flash API 封装
  - orchestrator:     主Agent编排器（全流程管控）
  - pre_agent:        译前Sub-Agent（agent说明书 + 工作流协调：策略 → 术语·分批全查）
  - translate_agent:  译中Sub-Agent（agent说明书 + 工作流协调：主译 → 一致性·条件触发）
  - post_agent:       译后Sub-Agent（agent说明书 + 工作流协调：质检 → 润色）
  - degradation:      异常降级处理（L0-L3）
"""

# D2: 导出框架核心类，方便外部使用
from transagent.backend.core.agent_framework import (
    BaseAgent, AgentContext, AgentResult, AgentStatus, RetryConfig,
    spawn, spawn_parallel, SpawnTask, AgentRegistry, register_agent,
    spawn_with_fallback, merge_results, make_spawn_task,
)

# D5: 导出技能框架与技能（目录化）
from transagent.backend.core.skills.skill import (
    Skill, SkillRegistry, register_skill,
)
from transagent.backend.core.skills.pre_skills.strategy_formulation.scripts.strategy_skill import StrategySkill
from transagent.backend.core.skills.pre_skills.term_extraction.scripts.term_skill import TermExtractionSkill
from transagent.backend.core.skills.translate_skills.chunk_translate.scripts.chunk_translate_skill import ChunkTranslateSkill
from transagent.backend.core.skills.translate_skills.consistency_fix.scripts.consistency_skill import ConsistencySkill
from transagent.backend.core.skills.post_skills.quality_inspection.scripts.quality_skill import QualityInspectionSkill
from transagent.backend.core.skills.post_skills.polish.scripts.polish_skill import PolishSkill

# 保持原有导出
from transagent.backend.core.llm_client import chat, chat_stream
from transagent.backend.core.orchestrator import Orchestrator, translate_document
from transagent.backend.core.pre_agent import spawn_pre_translate, PreTranslateAgent
from transagent.backend.core.translate_agent import (
    spawn_translate, spawn_translate_parallel, TranslateAgent,
)
from transagent.backend.core.post_agent import spawn_post_translate, PostTranslateAgent
from transagent.backend.core.degradation import handle_degradation
