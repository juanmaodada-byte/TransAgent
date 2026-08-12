"""
Sub-Agent 调用框架
==================
Vibe Coder A | v1.0 | 2026-08-07 (D2)

职责：为所有Sub-Agent提供统一的调用框架。

核心能力：
  - BaseAgent 抽象基类（生命周期钩子·可观测性）
  - AgentContext（取消令牌·超时·日志上下文·调用链追踪）
  - AgentResult（统一的结果容器·含计时·状态·错误）
  - spawn() 统一调用接口（超时·重试·取消）
  - spawn_parallel() 并行执行（max_concurrency可控）
  - AgentRegistry 全局注册与发现

设计原则：
  - 轻量：不引入LangGraph等重型框架，纯asyncio实现
  - 类型安全：输入/输出均可为 interface.py 中的 dataclass
  - 向后兼容：现有 spawn_* 函数继续工作
  - 可观测：内置计时、结构化日志、错误追踪

使用示例：
    # 方式1：直接用 spawn 包装现有函数
    result = await spawn(
        spawn_pre_translate, preprocess, user_prefs,
        context=AgentContext(agent_name="PreAgent", timeout_seconds=60),
    )

    # 方式2：通过 BaseAgent 子类
    agent = PreTranslateAgent()
    result = await agent.run(preprocess, user_prefs)

    # 方式3：并行执行
    results = await spawn_parallel([
        ("translate_chunk_1", spawn_chunk_translate, (chunk1,), {}),
        ("translate_chunk_2", spawn_chunk_translate, (chunk2,), {}),
    ])
"""

import asyncio
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional


# ══════════════════════════════════════════════════════════════════
# 一、基础类型
# ══════════════════════════════════════════════════════════════════

class AgentStatus(str, Enum):
    """Sub-Agent 执行状态"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 1               # 最大重试次数（0=不重试）
    delay_seconds: float = 1.0          # 重试基础延迟
    backoff_multiplier: float = 2.0     # 指数退避乘数
    retry_on_exceptions: tuple = (Exception,)  # 哪些异常触发重试


@dataclass
class AgentContext:
    """
    Sub-Agent 执行上下文。
    贯穿单次 agent 调用的完整生命周期，支持取消、超时、调用链追踪。
    """
    agent_name: str = ""                     # Agent名称（用于日志）
    timeout_seconds: float = 120.0           # 单次调用超时（秒）
    cancel_event: Optional[asyncio.Event] = None  # 外部取消信号
    parent_session_id: str = ""              # 所属TranslationSession ID
    span_id: str = ""                        # 本次调用的唯一span ID（追踪用）
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    metadata: dict = field(default_factory=dict)  # 额外上下文

    @property
    def is_cancelled(self) -> bool:
        """检查是否已被取消"""
        if self.cancel_event is not None:
            return self.cancel_event.is_set()
        return False

    def check_cancelled(self) -> None:
        """如果已取消则抛出异常"""
        if self.is_cancelled:
            raise AgentCancelledError(f"Agent '{self.agent_name}' was cancelled")

    @classmethod
    def simple(cls, name: str, timeout: float = 120.0) -> "AgentContext":
        """快速创建简单上下文"""
        import uuid
        return cls(
            agent_name=name,
            timeout_seconds=timeout,
            span_id=str(uuid.uuid4())[:8],
        )


@dataclass
class AgentResult:
    """
    统一的 Sub-Agent 执行结果容器。
    无论成功/失败/超时/取消，都通过此结构返回。
    """
    success: bool = False
    data: Any = None                       # 成功时的产出
    error: str = ""                        # 失败时的错误信息
    error_type: str = ""                   # 异常类型名
    status: AgentStatus = AgentStatus.IDLE
    elapsed_seconds: float = 0.0           # 纯执行耗时（不含重试等待）
    total_elapsed_seconds: float = 0.0     # 含重试等待的总耗时
    retry_count: int = 0                   # 实际重试次数
    agent_name: str = ""
    span_id: str = ""

    def unwrap(self):
        """成功时返回data，失败时抛出异常"""
        if not self.success:
            raise RuntimeError(f"Agent '{self.agent_name}' failed: {self.error}")
        return self.data

    def unwrap_or(self, default: Any) -> Any:
        """成功时返回data，失败时返回default"""
        return self.data if self.success else default

    def to_log_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "span_id": self.span_id,
            "success": self.success,
            "status": self.status.value,
            "elapsed": f"{self.elapsed_seconds:.2f}s",
            "total": f"{self.total_elapsed_seconds:.2f}s",
            "retries": self.retry_count,
            "error": self.error[:200] if self.error else "",
        }


# ══════════════════════════════════════════════════════════════════
# 二、异常类型
# ══════════════════════════════════════════════════════════════════

class AgentError(Exception):
    """Sub-Agent 调用相关异常的基类"""
    pass


class AgentTimeoutError(AgentError):
    """Agent执行超时"""
    pass


class AgentCancelledError(AgentError):
    """Agent被取消"""
    pass


class AgentMaxRetriesExceededError(AgentError):
    """Agent重试次数耗尽"""
    pass


# ══════════════════════════════════════════════════════════════════
# 三、核心 spawn 接口
# ══════════════════════════════════════════════════════════════════

async def spawn(
    agent_func: Callable[..., Awaitable[Any]],
    *args,
    context: Optional[AgentContext] = None,
    **kwargs,
) -> AgentResult:
    """
    统一的 Sub-Agent 调用接口。
    自动处理：超时控制·取消检测·自动重试·计时·异常捕获。

    Args:
        agent_func: 异步函数（如 spawn_pre_translate）
        *args: 传递给 agent_func 的位置参数
        context: 执行上下文（超时·取消·重试配置）
        **kwargs: 传递给 agent_func 的关键字参数

    Returns:
        AgentResult: 统一结果容器

    Example:
        result = await spawn(
            spawn_pre_translate, preprocess, user_prefs,
            context=AgentContext.simple("PreAgent", timeout=60),
        )
        if result.success:
            pre_result = result.data  # PreTranslateResult
    """
    ctx = context or AgentContext.simple(agent_func.__name__, timeout=120.0)
    ctx.agent_name = ctx.agent_name or agent_func.__name__

    retry_cfg = ctx.retry_config
    last_error = ""
    total_start = time.time()

    agent_result = AgentResult(
        agent_name=ctx.agent_name,
        span_id=ctx.span_id,
        status=AgentStatus.RUNNING,
    )

    for attempt in range(retry_cfg.max_retries + 1):
        # 每次重试前检查取消信号
        if ctx.is_cancelled:
            agent_result.status = AgentStatus.CANCELLED
            agent_result.error = f"Cancelled before attempt {attempt + 1}"
            agent_result.total_elapsed_seconds = time.time() - total_start
            return agent_result

        try:
            start = time.time()

            # 带超时执行
            agent_task = asyncio.ensure_future(agent_func(*args, **kwargs))

            if ctx.cancel_event is not None:
                # 同时等待 agent_task 和 cancel_event
                cancel_task = asyncio.ensure_future(ctx.cancel_event.wait())
                done, pending = await asyncio.wait(
                    [agent_task, cancel_task],
                    timeout=ctx.timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # 清理
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

                if cancel_task in done:
                    # 取消信号被触发
                    agent_result.status = AgentStatus.CANCELLED
                    agent_result.error = "Execution cancelled by external signal"
                    agent_result.elapsed_seconds = time.time() - start
                    agent_result.total_elapsed_seconds = time.time() - total_start
                    return agent_result

                if agent_task not in done:
                    # 超时
                    agent_result.status = AgentStatus.TIMEOUT
                    agent_result.error = f"Timeout after {ctx.timeout_seconds:.0f}s"
                    agent_result.elapsed_seconds = ctx.timeout_seconds
                    agent_result.total_elapsed_seconds = time.time() - total_start
                    last_error = agent_result.error
                    if attempt < retry_cfg.max_retries:
                        await _retry_delay(retry_cfg, attempt)
                        continue
                    return agent_result

                # agent_task 完成
                try:
                    result_data = agent_task.result()
                except Exception as e:
                    raise  # 转到外层异常处理
            else:
                # 无取消信号：简单超时
                try:
                    result_data = await asyncio.wait_for(
                        agent_task,
                        timeout=ctx.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    agent_result.status = AgentStatus.TIMEOUT
                    agent_result.error = f"Timeout after {ctx.timeout_seconds:.0f}s"
                    agent_result.elapsed_seconds = ctx.timeout_seconds
                    agent_result.total_elapsed_seconds = time.time() - total_start
                    last_error = agent_result.error
                    if attempt < retry_cfg.max_retries:
                        await _retry_delay(retry_cfg, attempt)
                        continue
                    return agent_result

            # 成功
            elapsed = time.time() - start
            agent_result.success = True
            agent_result.data = result_data
            agent_result.status = AgentStatus.COMPLETED
            agent_result.elapsed_seconds = elapsed
            agent_result.retry_count = attempt
            agent_result.total_elapsed_seconds = time.time() - total_start

            _log_agent_completion(agent_result, ctx)
            return agent_result

        except asyncio.CancelledError:
            agent_result.status = AgentStatus.CANCELLED
            agent_result.error = "Task cancelled"
            agent_result.elapsed_seconds = time.time() - start if 'start' in dir() else 0
            agent_result.total_elapsed_seconds = time.time() - total_start
            return agent_result

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            is_retryable = isinstance(e, retry_cfg.retry_on_exceptions)

            if is_retryable and attempt < retry_cfg.max_retries:
                print(f"[Framework] {ctx.agent_name} attempt {attempt + 1} failed: {last_error} — retrying...")
                await _retry_delay(retry_cfg, attempt)
                continue

            # 不重试 或 重试耗尽
            elapsed = time.time() - start if 'start' in dir() else 0
            agent_result.success = False
            agent_result.error = last_error
            agent_result.error_type = type(e).__name__
            agent_result.status = AgentStatus.FAILED
            agent_result.elapsed_seconds = elapsed
            agent_result.retry_count = attempt
            agent_result.total_elapsed_seconds = time.time() - total_start

            _log_agent_failure(agent_result, ctx, e)
            return agent_result

    # 理论上不会到这里（所有路径都有return），但兜底
    agent_result.success = False
    agent_result.error = last_error
    agent_result.status = AgentStatus.FAILED
    agent_result.total_elapsed_seconds = time.time() - total_start
    return agent_result


async def _retry_delay(cfg: RetryConfig, attempt: int) -> None:
    """计算并等待重试延迟"""
    delay = cfg.delay_seconds * (cfg.backoff_multiplier ** attempt)
    await asyncio.sleep(delay)


def _log_agent_completion(result: AgentResult, ctx: AgentContext) -> None:
    """结构化日志：成功"""
    print(f"[Framework] ✓ {ctx.agent_name} completed in {result.elapsed_seconds:.1f}s"
          + (f" (retried {result.retry_count}x)" if result.retry_count else ""))


def _log_agent_failure(result: AgentResult, ctx: AgentContext, exc: Exception) -> None:
    """结构化日志：失败"""
    print(f"[Framework] ✗ {ctx.agent_name} FAILED after {result.elapsed_seconds:.1f}s"
          + (f" (retried {result.retry_count}x)" if result.retry_count else "")
          + f": {result.error[:150]}")


# ══════════════════════════════════════════════════════════════════
# 四、并行执行
# ══════════════════════════════════════════════════════════════════

@dataclass
class SpawnTask:
    """单个 spawn 任务描述"""
    name: str                              # 任务名（用于日志）
    func: Callable[..., Awaitable[Any]]    # 要执行的异步函数
    args: tuple = ()                       # 位置参数
    kwargs: dict = field(default_factory=dict)  # 关键字参数
    context: Optional[AgentContext] = None  # 独立的上下文（None=自动生成）


async def spawn_parallel(
    tasks: list[SpawnTask],
    max_concurrency: int = 8,
    parent_context: Optional[AgentContext] = None,
    cancel_on_first_failure: bool = False,
) -> list[AgentResult]:
    """
    并行执行多个 Sub-Agent 调用。

    Args:
        tasks: 任务列表
        max_concurrency: 最大并发数（默认8）
        parent_context: 父级上下文（会自动为每个子任务生成span_id）
        cancel_on_first_failure: 第一个失败时是否取消其余任务

    Returns:
        list[AgentResult]: 与tasks同序的结果列表

    Example:
        results = await spawn_parallel([
            SpawnTask("chunk_1", translate_chunk, (chunk1, term_table)),
            SpawnTask("chunk_2", translate_chunk, (chunk2, term_table)),
            SpawnTask("chunk_3", translate_chunk, (chunk3, term_table)),
        ], max_concurrency=3)
    """
    import uuid

    # 信号量控制并发
    semaphore = asyncio.Semaphore(max_concurrency)
    cancel_event = asyncio.Event()  # 用于 cancel_on_first_failure
    results: list[Optional[AgentResult]] = [None] * len(tasks)

    async def _run_one(index: int, task: SpawnTask) -> None:
        async with semaphore:
            # 如果已经触发全局取消，直接跳过
            if cancel_event.is_set():
                results[index] = AgentResult(
                    agent_name=task.name,
                    status=AgentStatus.CANCELLED,
                    error="Cancelled due to sibling failure",
                )
                return

            # 构建上下文：合并 parent_context
            ctx = task.context or AgentContext(
                agent_name=task.name,
                span_id=str(uuid.uuid4())[:8],
            )
            if parent_context:
                ctx.parent_session_id = ctx.parent_session_id or parent_context.parent_session_id
                if parent_context.cancel_event is not None:
                    # 子任务同时监听父取消信号和全局取消信号
                    combined = asyncio.Event()
                    # 用简单方式：直接复用parent的cancel_event
                    ctx.cancel_event = parent_context.cancel_event
                ctx.metadata = {**parent_context.metadata, **ctx.metadata}
                if not ctx.timeout_seconds or ctx.timeout_seconds > parent_context.timeout_seconds:
                    ctx.timeout_seconds = parent_context.timeout_seconds

            result = await spawn(task.func, *task.args, context=ctx, **task.kwargs)
            results[index] = result

            if cancel_on_first_failure and not result.success:
                cancel_event.set()

    # 并行执行所有任务
    await asyncio.gather(*[
        _run_one(i, task) for i, task in enumerate(tasks)
    ], return_exceptions=True)

    # 处理 gather 级别的异常（极少发生·兜底）
    final_results: list[AgentResult] = []
    for i, r in enumerate(results):
        if r is None:
            final_results.append(AgentResult(
                agent_name=tasks[i].name,
                status=AgentStatus.FAILED,
                error="Task produced no result (gather-level failure)",
            ))
        else:
            final_results.append(r)

    # 汇总日志
    succeeded = sum(1 for r in final_results if r.success)
    failed = len(final_results) - succeeded
    print(f"[Framework] spawn_parallel complete: {succeeded} succeeded, {failed} failed (total {len(final_results)})")

    return final_results


# ══════════════════════════════════════════════════════════════════
# 五、BaseAgent 抽象基类
# ══════════════════════════════════════════════════════════════════

class BaseAgent(ABC):
    """
    Sub-Agent 抽象基类。

    子类只需实现：
      - execute(input_data) → 核心业务逻辑
      - agent_name 属性

    可选覆盖：
      - on_start() / on_success() / on_failure() → 生命周期钩子
      - default_context() → 自定义默认上下文
    """

    def __init__(self, context: Optional[AgentContext] = None):
        self.context = context or self.default_context()
        if not self.context.agent_name:
            self.context.agent_name = self.agent_name

    # ── 子类必须实现 ──

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Agent名称（用于日志和注册）"""
        ...

    @abstractmethod
    async def execute(self, *args, **kwargs) -> Any:
        """核心业务逻辑。子类实现。"""
        ...

    # ── 可选覆盖 ──

    def default_context(self) -> AgentContext:
        """默认执行上下文。子类可覆盖以自定义超时/重试。"""
        return AgentContext.simple(self.agent_name)

    async def on_start(self, *args, **kwargs) -> None:
        """执行前钩子"""
        pass

    async def on_success(self, result: Any) -> None:
        """成功后钩子"""
        pass

    async def on_failure(self, error: Exception) -> None:
        """失败后钩子"""
        pass

    async def on_cancelled(self) -> None:
        """取消后钩子"""
        pass

    # ── 公开接口 ──

    async def run(self, *args, **kwargs) -> AgentResult:
        """
        执行 Sub-Agent（框架入口）。
        自动处理生命周期钩子、超时、重试、取消。
        """
        await self.on_start(*args, **kwargs)

        async def _execute_wrapped():
            return await self.execute(*args, **kwargs)

        result = await spawn(_execute_wrapped, context=self.context)

        if result.status == AgentStatus.CANCELLED:
            await self.on_cancelled()
        elif not result.success:
            try:
                await self.on_failure(Exception(result.error))
            except Exception:
                pass
        else:
            await self.on_success(result.data)

        return result


# ══════════════════════════════════════════════════════════════════
# 六、Agent 注册中心
# ══════════════════════════════════════════════════════════════════

class AgentRegistry:
    """
    全局 Sub-Agent 注册中心。
    支持按名称查找、获取所有已注册Agent。
    """

    _agents: dict[str, type[BaseAgent]] = {}
    _instances: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, agent_cls: type[BaseAgent]) -> type[BaseAgent]:
        """装饰器：注册一个Agent类"""
        name = agent_cls.__name__
        cls._agents[name] = agent_cls
        return agent_cls

    @classmethod
    def get(cls, name: str) -> Optional[type[BaseAgent]]:
        """按名称查找Agent类"""
        return cls._agents.get(name)

    @classmethod
    def create(cls, name: str, context: Optional[AgentContext] = None) -> Optional[BaseAgent]:
        """按名称创建Agent实例"""
        agent_cls = cls._agents.get(name)
        if agent_cls:
            return agent_cls(context=context)
        return None

    @classmethod
    def list_all(cls) -> list[str]:
        """列出所有已注册Agent名称"""
        return list(cls._agents.keys())

    @classmethod
    def clear(cls) -> None:
        """清空注册（测试用）"""
        cls._agents.clear()
        cls._instances.clear()


def register_agent(cls: type[BaseAgent]) -> type[BaseAgent]:
    """装饰器语法糖：@register_agent"""
    return AgentRegistry.register(cls)


# ══════════════════════════════════════════════════════════════════
# 七、便捷工具
# ══════════════════════════════════════════════════════════════════

def make_spawn_task(
    name: str,
    func: Callable[..., Awaitable[Any]],
    *args,
    timeout: float = 120.0,
    **kwargs,
) -> SpawnTask:
    """快速创建 SpawnTask"""
    return SpawnTask(
        name=name,
        func=func,
        args=args,
        kwargs=kwargs,
        context=AgentContext.simple(name, timeout=timeout),
    )


async def spawn_with_fallback(
    primary_task: SpawnTask,
    fallback_task: SpawnTask,
) -> AgentResult:
    """
    先尝试 primary，失败则自动切到 fallback。
    用于模型降级等场景。
    """
    result = await spawn(
        primary_task.func,
        *primary_task.args,
        context=primary_task.context,
        **primary_task.kwargs,
    )
    if result.success:
        return result

    print(f"[Framework] Primary '{primary_task.name}' failed, falling back to '{fallback_task.name}'")
    return await spawn(
        fallback_task.func,
        *fallback_task.args,
        context=fallback_task.context,
        **fallback_task.kwargs,
    )


def merge_results(results: list[AgentResult], default_value: Any = "") -> Any:
    """
    合并多个 AgentResult 的 data。
    - 全部为str → 拼接
    - 全部为list → 展平
    - 否则 → 返回list
    """
    datas = [r.data for r in results if r.success and r.data is not None]
    if not datas:
        return default_value
    if all(isinstance(d, str) for d in datas):
        return "\n\n".join(datas)
    if all(isinstance(d, list) for d in datas):
        flat = []
        for d in datas:
            flat.extend(d)
        return flat
    return datas
