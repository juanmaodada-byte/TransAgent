"""D2 Import verification — run after D2 changes to confirm all imports resolve."""
import sys, io
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print("=== D2 Import Verification ===\n")

# 1. Framework core
print("1. agent_framework...", end=" ")
from transagent.backend.core.agent_framework import (
    AgentStatus, AgentContext, AgentResult, RetryConfig,
    BaseAgent, AgentRegistry, register_agent,
    spawn, spawn_parallel, SpawnTask,
    make_spawn_task, spawn_with_fallback, merge_results,
    AgentError, AgentTimeoutError, AgentCancelledError,
)
print("OK")

# 2. PreTranslateAgent (BaseAgent subclass)
print("2. PreTranslateAgent...", end=" ")
from transagent.backend.core.pre_agent import spawn_pre_translate, PreTranslateAgent
print("OK")

# 3. TranslateAgent (BaseAgent subclass + parallel)
print("3. TranslateAgent...", end=" ")
from transagent.backend.core.translate_agent import spawn_translate, spawn_translate_parallel, TranslateAgent
print("OK")

# 4. PostTranslateAgent (BaseAgent subclass)
print("4. PostTranslateAgent...", end=" ")
from transagent.backend.core.post_agent import spawn_post_translate, PostTranslateAgent
print("OK")

# 5. Orchestrator (framework integration)
print("5. Orchestrator...", end=" ")
from transagent.backend.core.orchestrator import Orchestrator, translate_document
print("OK")

# 6. LLM client (unchanged)
print("6. LLM Client...", end=" ")
from transagent.backend.core.llm_client import chat, chat_stream
print("OK")

# 7. Degradation (unchanged)
print("7. Degradation...", end=" ")
from transagent.backend.core.degradation import handle_degradation
print("OK")

# 8. Agent registry check
print("8. AgentRegistry...", end=" ")
agents = AgentRegistry.list_all()
print(f"OK ({len(agents)} registered: {agents})")

# 9. Verify BaseAgent inheritance
print("9. Class hierarchy...", end=" ")
assert issubclass(PreTranslateAgent, BaseAgent)
assert issubclass(TranslateAgent, BaseAgent)
assert issubclass(PostTranslateAgent, BaseAgent)
print("OK (all 3 agents extend BaseAgent)")

# 10. Verify backward compatibility (old spawn functions still work)
print("10. Backward compat...", end=" ")
assert callable(spawn_pre_translate)
assert callable(spawn_translate)
assert callable(spawn_post_translate)
print("OK (all 3 spawn_* functions remain callable)")

# 11. Verify orchestrator has new D2 attributes
print("11. Orchestrator D2 attrs...", end=" ")
o = Orchestrator("test_user")
assert hasattr(o, 'parallel_chunks')
assert hasattr(o, 'max_chunk_concurrency')
assert o.parallel_chunks == False
assert o.max_chunk_concurrency == 4
print(f"OK (parallel_chunks={o.parallel_chunks}, max_concurrency={o.max_chunk_concurrency})")

print(f"\n=== All imports verified! D2 framework is ready. ===")
