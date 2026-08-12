"""
Sub-Agent 调用框架测试
======================
D2 验证脚本：测试 agent_framework 的核心功能，不使用真实LLM调用。

测试范围：
  1. AgentContext 创建与取消检测
  2. spawn() — 正常执行、超时、取消、重试
  3. spawn_parallel() — 并行执行、max_concurrency、cancel_on_first_failure
  4. BaseAgent 生命周期钩子
  5. AgentRegistry 注册与发现
  6. SpawnTask / make_spawn_task 便捷函数
  7. merge_results 合并工具
  8. spawn_with_fallback 降级切换

用法：
    cd transagent
    python -X utf8 tests/test_agent_framework.py
"""

import sys, io, asyncio, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from transagent.backend.core.agent_framework import (
    # 类型
    AgentStatus, AgentContext, AgentResult, RetryConfig,
    # 核心
    spawn, spawn_parallel, SpawnTask,
    # 基类
    BaseAgent, AgentRegistry, register_agent,
    # 异常
    AgentError, AgentTimeoutError, AgentCancelledError,
    # 工具
    make_spawn_task, spawn_with_fallback, merge_results,
)

SEP = "=" * 70
SUB = "-" * 50

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def print_header(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


# ══════════════════════════════════════════════════════════════════
# 测试辅助
# ══════════════════════════════════════════════════════════════════

async def fast_func(value: str = "ok", delay: float = 0.01) -> str:
    """模拟快速成功的异步函数"""
    await asyncio.sleep(delay)
    return value


async def slow_func(delay: float = 10.0) -> str:
    """模拟慢速函数（测试超时）"""
    await asyncio.sleep(delay)
    return "done"


async def flaky_func(fail_times: int = 2) -> str:
    """前N次失败，之后成功（测试重试）"""
    flaky_func.call_count += 1
    if flaky_func.call_count <= fail_times:
        raise ValueError(f"Flaky failure #{flaky_func.call_count}")
    return f"success_on_attempt_{flaky_func.call_count}"

flaky_func.call_count = 0


async def cancellable_func(cancel_event: asyncio.Event) -> str:
    """可通过取消信号中断的函数"""
    for i in range(100):
        if cancel_event.is_set():
            raise asyncio.CancelledError("Cancelled during work")
        await asyncio.sleep(0.01)
    return "completed"


# ══════════════════════════════════════════════════════════════════
# Test 1: AgentContext
# ══════════════════════════════════════════════════════════════════

async def test_agent_context():
    print_header("Test 1: AgentContext")

    ctx = AgentContext.simple("TestAgent", timeout=30.0)
    check("simple() creates context", ctx.agent_name == "TestAgent")
    check("simple() sets timeout", ctx.timeout_seconds == 30.0)
    check("simple() generates span_id", len(ctx.span_id) > 0)

    # 取消检测
    check("is_cancelled=False when no event", not ctx.is_cancelled)

    evt = asyncio.Event()
    ctx2 = AgentContext(agent_name="T2", cancel_event=evt)
    check("is_cancelled=False before set", not ctx2.is_cancelled)
    evt.set()
    check("is_cancelled=True after set", ctx2.is_cancelled)

    # check_cancelled 应抛出
    try:
        ctx2.check_cancelled()
        check("check_cancelled raises", False, "should have raised")
    except AgentCancelledError:
        check("check_cancelled raises AgentCancelledError", True)


# ══════════════════════════════════════════════════════════════════
# Test 2: spawn() — 正常执行
# ══════════════════════════════════════════════════════════════════

async def test_spawn_success():
    print_header("Test 2: spawn() — Normal Execution")

    result = await spawn(fast_func, "hello", context=AgentContext.simple("T1"))
    check("success=True", result.success)
    check("status=COMPLETED", result.status == AgentStatus.COMPLETED)
    check("data preserved", result.data == "hello")
    check("elapsed tracked", result.elapsed_seconds > 0)
    check("agent_name recorded", result.agent_name == "T1")

    # unwrap
    check("unwrap() returns data", result.unwrap() == "hello")
    check("unwrap_or() returns data", result.unwrap_or("fallback") == "hello")


# ══════════════════════════════════════════════════════════════════
# Test 3: spawn() — 超时
# ══════════════════════════════════════════════════════════════════

async def test_spawn_timeout():
    print_header("Test 3: spawn() — Timeout")

    result = await spawn(
        slow_func, 10.0,
        context=AgentContext(
            agent_name="SlowAgent",
            timeout_seconds=0.1,
            retry_config=RetryConfig(max_retries=0),
        ),
    )
    check("success=False on timeout", not result.success)
    check("status=TIMEOUT", result.status == AgentStatus.TIMEOUT)
    check("error contains 'Timeout'", "Timeout" in result.error)
    check("unwrap_or returns fallback", result.unwrap_or("fallback") == "fallback")


# ══════════════════════════════════════════════════════════════════
# Test 4: spawn() — 取消
# ══════════════════════════════════════════════════════════════════

async def test_spawn_cancellation():
    print_header("Test 4: spawn() — Cancellation")

    cancel_evt = asyncio.Event()

    async def _cancel_soon():
        await asyncio.sleep(0.05)
        cancel_evt.set()

    ctx = AgentContext(
        agent_name="CancelTest",
        cancel_event=cancel_evt,
        timeout_seconds=5.0,
    )

    # 同时运行 cancellable_func 和 cancel_soon
    spawn_task = asyncio.ensure_future(
        spawn(cancellable_func, cancel_evt, context=ctx)
    )
    cancel_task = asyncio.ensure_future(_cancel_soon())

    result = await spawn_task
    await cancel_task

    check("success=False when cancelled", not result.success)
    check("status=CANCELLED", result.status == AgentStatus.CANCELLED)


# ══════════════════════════════════════════════════════════════════
# Test 5: spawn() — 重试
# ══════════════════════════════════════════════════════════════════

async def test_spawn_retry():
    print_header("Test 5: spawn() — Retry")

    flaky_func.call_count = 0  # reset

    result = await spawn(
        flaky_func, 2,  # 前2次失败，第3次成功
        context=AgentContext(
            agent_name="RetryTest",
            timeout_seconds=10.0,
            retry_config=RetryConfig(
                max_retries=3,
                delay_seconds=0.01,
                backoff_multiplier=1.0,
            ),
        ),
    )
    check("success=True after retries", result.success)
    check("data correct", "success_on_attempt" in str(result.data))
    check("retry_count tracked", result.retry_count == 2)

    # 重试耗尽
    flaky_func.call_count = 0
    result2 = await spawn(
        flaky_func, 10,  # 前10次失败（远超max_retries）
        context=AgentContext(
            agent_name="RetryExhausted",
            timeout_seconds=10.0,
            retry_config=RetryConfig(
                max_retries=2,
                delay_seconds=0.01,
            ),
        ),
    )
    check("success=False when retries exhausted", not result2.success)
    check("status=FAILED", result2.status == AgentStatus.FAILED)


# ══════════════════════════════════════════════════════════════════
# Test 6: spawn_parallel() — 并行执行
# ══════════════════════════════════════════════════════════════════

async def test_spawn_parallel():
    print_header("Test 6: spawn_parallel()")

    # 创建5个快速任务
    tasks = [
        SpawnTask(
            name=f"task_{i}",
            func=fast_func,
            args=(f"result_{i}",),
            kwargs={"delay": 0.02},
        )
        for i in range(5)
    ]

    t0 = time.time()
    results = await spawn_parallel(tasks, max_concurrency=5)
    elapsed = time.time() - t0

    check("all 5 results returned", len(results) == 5)
    check("all succeeded", all(r.success for r in results))
    check("parallel speed (5 tasks < 0.2s total)", elapsed < 0.2,
          f"elapsed={elapsed:.3f}s")

    for i, r in enumerate(results):
        check(f"  task_{i} data correct", r.data == f"result_{i}")


# ══════════════════════════════════════════════════════════════════
# Test 7: spawn_parallel() — max_concurrency
# ══════════════════════════════════════════════════════════════════

async def test_spawn_parallel_concurrency():
    print_header("Test 7: spawn_parallel() — max_concurrency")

    running = 0
    max_running = 0
    lock = asyncio.Lock()

    async def tracked_task(task_id: int):
        nonlocal running, max_running
        async with lock:
            running += 1
            max_running = max(max_running, running)
        await asyncio.sleep(0.03)
        async with lock:
            running -= 1
        return f"done_{task_id}"

    tasks = [
        SpawnTask(name=f"t_{i}", func=tracked_task, args=(i,))
        for i in range(10)
    ]

    results = await spawn_parallel(tasks, max_concurrency=3)
    check("all 10 succeeded", all(r.success for r in results))
    check("max concurrency respected", max_running <= 3,
          f"max_running={max_running}")
    print(f"  (max concurrent tasks observed: {max_running})")


# ══════════════════════════════════════════════════════════════════
# Test 8: spawn_parallel() — cancel_on_first_failure
# ══════════════════════════════════════════════════════════════════

async def test_spawn_parallel_cancel():
    print_header("Test 8: spawn_parallel() — cancel_on_first_failure")

    async def fail_early(task_id: int):
        if task_id == 2:
            raise RuntimeError("Intentional failure")
        await asyncio.sleep(0.05)
        return f"ok_{task_id}"

    tasks = [
        SpawnTask(name=f"t_{i}", func=fail_early, args=(i,))
        for i in range(5)
    ]

    results = await spawn_parallel(tasks, max_concurrency=5, cancel_on_first_failure=True)
    check("task 2 failed", not results[2].success)
    check("task 2 has correct error", "Intentional failure" in results[2].error)


# ══════════════════════════════════════════════════════════════════
# Test 9: BaseAgent 生命周期
# ══════════════════════════════════════════════════════════════════

async def test_base_agent():
    print_header("Test 9: BaseAgent — Lifecycle Hooks")

    lifecycle_log = []

    @register_agent
    class TestAgent(BaseAgent):
        @property
        def agent_name(self) -> str:
            return "TestAgent"

        async def execute(self, value: str) -> str:
            lifecycle_log.append("execute")
            return f"processed_{value}"

        async def on_start(self, *args, **kwargs):
            lifecycle_log.append("on_start")

        async def on_success(self, result):
            lifecycle_log.append(f"on_success:{result}")

        async def on_failure(self, error):
            lifecycle_log.append(f"on_failure:{error}")

    agent = TestAgent(context=AgentContext.simple("TestAgent", timeout=5.0))
    result = await agent.run("hello")

    check("execute succeeded", result.success)
    check("data correct", result.data == "processed_hello")
    check("lifecycle order correct",
          lifecycle_log == ["on_start", "execute", "on_success:processed_hello"],
          f"got: {lifecycle_log}")

    # 测试 failure 钩子
    @register_agent
    class FailingAgent(BaseAgent):
        @property
        def agent_name(self) -> str:
            return "FailingAgent"

        async def execute(self) -> str:
            raise ValueError("Boom")

        async def on_failure(self, error):
            lifecycle_log.clear()
            lifecycle_log.append(f"on_failure:{type(error).__name__}")

    agent2 = FailingAgent(context=AgentContext(
        agent_name="FailTest",
        timeout_seconds=5.0,
        retry_config=RetryConfig(max_retries=0),
    ))
    result2 = await agent2.run()
    check("failure captured", not result2.success)
    check("on_failure called", "on_failure" in lifecycle_log[0])


# ══════════════════════════════════════════════════════════════════
# Test 10: AgentRegistry
# ══════════════════════════════════════════════════════════════════

async def test_agent_registry():
    print_header("Test 10: AgentRegistry")

    # 前面的测试已经注册了 TestAgent 和 FailingAgent
    agents = AgentRegistry.list_all()
    check("registry has agents", len(agents) >= 2, f"registered: {agents}")

    cls = AgentRegistry.get("TestAgent")
    check("get('TestAgent') returns class", cls is not None)

    instance = AgentRegistry.create("TestAgent")
    check("create('TestAgent') returns instance", instance is not None)
    check("instance is BaseAgent", isinstance(instance, BaseAgent))

    # 不存在的agent
    check("get('NonExistent') returns None", AgentRegistry.get("NonExistent") is None)
    check("create('NonExistent') returns None", AgentRegistry.create("NonExistent") is None)


# ══════════════════════════════════════════════════════════════════
# Test 11: make_spawn_task / merge_results / spawn_with_fallback
# ══════════════════════════════════════════════════════════════════

async def test_utilities():
    print_header("Test 11: Utility Functions")

    # make_spawn_task
    task = make_spawn_task("test", fast_func, "hello", timeout=5.0)
    check("make_spawn_task creates SpawnTask", isinstance(task, SpawnTask))
    check("  name correct", task.name == "test")
    check("  args correct", task.args == ("hello",))

    # merge_results — strings
    r1 = AgentResult(success=True, data="part1")
    r2 = AgentResult(success=True, data="part2")
    r3 = AgentResult(success=False, data="ignored")
    merged = merge_results([r1, r2, r3])
    check("merge_results joins strings", merged == "part1\n\npart2")

    # merge_results — all failed
    merged2 = merge_results([r3], default_value="fallback")
    check("merge_results returns default on all-fail", merged2 == "fallback")

    # spawn_with_fallback
    async def always_fail():
        raise RuntimeError("Primary down")

    async def always_ok():
        return "fallback_result"

    primary = SpawnTask(
        name="primary", func=always_fail,
        context=AgentContext.simple("primary", timeout=2.0),
    )
    fallback = SpawnTask(
        name="fallback", func=always_ok,
        context=AgentContext.simple("fallback", timeout=2.0),
    )

    result = await spawn_with_fallback(primary, fallback)
    check("spawn_with_fallback uses fallback", result.success)
    check("  data is from fallback", result.data == "fallback_result")


# ══════════════════════════════════════════════════════════════════
# Test 12: AgentResult to_log_dict
# ══════════════════════════════════════════════════════════════════

async def test_result_logging():
    print_header("Test 12: AgentResult.to_log_dict()")

    success_result = AgentResult(
        success=True, status=AgentStatus.COMPLETED,
        agent_name="Test", span_id="abc123",
        elapsed_seconds=2.5, total_elapsed_seconds=2.5,
    )
    log = success_result.to_log_dict()
    check("to_log_dict has agent", log["agent"] == "Test")
    check("to_log_dict has elapsed", "2.50s" in log["elapsed"])

    fail_result = AgentResult(
        success=False, status=AgentStatus.FAILED,
        agent_name="Fail", error="Something broke",
    )
    log2 = fail_result.to_log_dict()
    check("to_log_dict captures error", "Something broke" in log2["error"])


# ══════════════════════════════════════════════════════════════════
# Test 13: spawn() with kwargs
# ══════════════════════════════════════════════════════════════════

async def test_spawn_kwargs():
    print_header("Test 13: spawn() — Keyword Arguments")

    async def func_with_kwargs(a: str, b: int = 0, c: str = "default") -> dict:
        return {"a": a, "b": b, "c": c}

    result = await spawn(
        func_with_kwargs, "hello",
        b=42, c="custom",
        context=AgentContext.simple("KwargsTest"),
    )
    check("kwargs passed correctly", result.success)
    check("  a=hello", result.data["a"] == "hello")
    check("  b=42", result.data["b"] == 42)
    check("  c=custom", result.data["c"] == "custom")


# ══════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════

async def main():
    global passed, failed

    print_header("TransAgent D2 — Agent Framework Tests")
    print(f"  (unit tests, no LLM calls)")

    # 清理注册表（避免跨测试污染）
    AgentRegistry.clear()

    await test_agent_context()
    await test_spawn_success()
    await test_spawn_timeout()
    await test_spawn_cancellation()
    await test_spawn_retry()
    await test_spawn_parallel()
    await test_spawn_parallel_concurrency()
    await test_spawn_parallel_cancel()
    await test_base_agent()
    await test_agent_registry()
    await test_utilities()
    await test_result_logging()
    await test_spawn_kwargs()

    # ── 总结 ──
    print_header("D2 Framework Test Summary")
    total = passed + failed
    print(f"  Passed: {passed}/{total}")
    if failed > 0:
        print(f"  Failed: {failed}/{total}")
        print(f"\n  ⚠ {failed} test(s) failed!")
    else:
        print(f"\n  >>> All {passed} tests passed! <<<")


if __name__ == "__main__":
    asyncio.run(main())
