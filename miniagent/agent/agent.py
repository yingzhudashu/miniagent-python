"""Mini Agent Python — Agent 编排层（两阶段主入口）

**对外两阶段**（本模块核心职责）：
- **Phase 1（Planning）**：调用 :mod:`miniagent.agent.planner`，产出 ``StructuredPlan``；在
  ``skip_planning``、无工具箱、或任务分类为「简单」时可跳过并回落默认计划。
- **Phase 2（Execution）**：调用 :mod:`miniagent.agent.executor` 的 ReAct 循环直至无工具调用或达上限。

**可选编排步骤**（由 ``run_agent`` 串联，不改变上述两阶段定义）：
- 任务分类（Phase 0）、需求澄清（Phase 0.5）、执行后反思（Phase 3）。

**边界**：本模块不处理 stdin/stdout、消息队列或飞书 HTTP；仅编排 LLM 与工具。通道相关回调通过
``on_thinking`` / ``on_tool_call`` 等注入，由 :class:`miniagent.assistant.engine.engine.UnifiedEngine` 等上层接线。
规划可见输出合并为 ``[评估与计划]`` 流式段；可选关键字参数 ``full_record`` 由引擎用于会话历史全量落盘（见 ``miniagent.agent.thinking_callback.invoke_on_thinking``）。

**轮数上限（与执行器一致）**：全局 ReAct 上限由 ``agent.max_turns``（默认 400）控制；分步模式下单步上限为 Internal 常量 ``EXECUTION_STEP_MAX_TURNS``。规划器给出的建议轮数**不会**把上述硬上限压低。

**导出**：``run_agent``、``run_pipeline``、常量 ``PLANNING_STREAM_HEADER``。

设计背景见 ``docs/ARCHITECTURE.md``（两阶段管线）。
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from miniagent.agent.activity import invoke_activity_log
from miniagent.agent.agent_defaults import (
    create_default_plan as _create_default_plan,
)
from miniagent.agent.agent_defaults import (
    user_forbids_tools as _user_forbids_tools,
)
from miniagent.agent.agent_display import (
    format_plan_display_short as _format_plan_display_short,
)
from miniagent.agent.agent_display import (
    format_plan_message as _format_plan_message,
)
from miniagent.agent.agent_display import (
    format_task_difficulty as _format_task_difficulty,
)
from miniagent.agent.config import get_default_agent_config, merge_agent_config
from miniagent.agent.constants import (
    CLARIFIER_MAX_QUESTIONS_COMPLEX,
    CLARIFIER_MAX_QUESTIONS_MEDIUM,
    CLARIFIER_MAX_QUESTIONS_NORMAL,
    CLARIFIER_MAX_QUESTIONS_SIMPLE,
    EXECUTION_MAX_PLAN_CONFIRM_ROUNDS,
)
from miniagent.agent.executor import execute_plan
from miniagent.agent.logging import get_logger
from miniagent.agent.monitor import DefaultToolMonitor
from miniagent.agent.pipeline import run_pipeline
from miniagent.agent.plan_utils import resolve_effective_overflow_strategy
from miniagent.agent.planner import generate_plan
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.ports.runtime import OnThinkingCallback, OnToolFinishCallback
from miniagent.agent.problem_solver import build_reflection_footer, reflect_on_result
from miniagent.agent.settings import get_config
from miniagent.agent.task_classifier import (
    TaskDifficulty,
    classify_task_difficulty,
    default_step_thinking_for_difficulty,
    exec_merge_for_simple_path,
    planner_merge_for_difficulty,
    task_classifier_enabled,
)
from miniagent.agent.thinking_callback import invoke_on_thinking
from miniagent.agent.thinking_presets import map_business_depth
from miniagent.agent.types.agent import (
    AgentRunOptions,
    AgentRunResult,
    ToolMonitorProtocol,
    ToolStats,
)
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.confirmation import (
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationStage,
)
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.agent.types.planning import (
    ContextStrategy,
    StructuredPlan,
    SuggestedConfig,
)
from miniagent.agent.types.tool import Toolbox, ToolRegistryProtocol

_logger = get_logger(__name__)


def _announce_difficulty_and_plan_enabled() -> bool:
    """检查是否向用户展示任务难度与规划摘要。

    从常量 EXECUTION_ANNOUNCE_DIFFICULTY 读取配置，用于控制 [评估与计划] 段
    是否在思考流中可见。关闭后规划过程对用户透明，可减少信息过载。

    Returns:
        bool: True 表示展示难度与规划，False 表示静默执行。

    Note:
        该配置影响 on_thinking 回调的 PLANNING_STREAM_HEADER 段推送。
    """
    from miniagent.agent.constants import EXECUTION_ANNOUNCE_DIFFICULTY

    return EXECUTION_ANNOUNCE_DIFFICULTY


def _clarifier_max_questions_for_difficulty(difficulty: TaskDifficulty) -> int:
    """按任务难度返回澄清追问上限（见 ``core.constants`` CLARIFIER_MAX_QUESTIONS_*）。"""
    if difficulty == TaskDifficulty.SIMPLE:
        return CLARIFIER_MAX_QUESTIONS_SIMPLE
    if difficulty == TaskDifficulty.NORMAL:
        return CLARIFIER_MAX_QUESTIONS_NORMAL
    if difficulty == TaskDifficulty.MEDIUM:
        return CLARIFIER_MAX_QUESTIONS_MEDIUM
    return CLARIFIER_MAX_QUESTIONS_COMPLEX


PLANNING_STREAM_HEADER = "[评估与计划]"


def _merge_plan_suggested_config(plan: StructuredPlan, merged_config: AgentConfig) -> AgentConfig:
    """合并规划器建议配置与计划风险等级到运行配置。

    ``suggested_config.max_turns`` 仅抬高基线、不压低；``parallelism`` 映射为
    ``allow_parallel_tools``；``context_overflow_strategy`` 可来自 suggested_config 或
    :func:`miniagent.agent.plan_utils.resolve_effective_overflow_strategy`。
    """
    config = merged_config
    if plan.suggested_config:
        sc = plan.suggested_config
        overrides: dict[str, Any] = {}
        if sc.max_turns is not None:
            overrides["max_turns"] = max(config.max_turns, sc.max_turns)
        if sc.tool_timeout is not None:
            overrides["tool_timeout"] = sc.tool_timeout
        if sc.risk_level is not None:
            overrides["risk_level"] = sc.risk_level
        if sc.context_overflow_strategy is not None:
            overrides["context_overflow_strategy"] = sc.context_overflow_strategy
        elif plan.context_strategy:
            overflow = resolve_effective_overflow_strategy(plan, config.context_overflow_strategy)
            overrides["context_overflow_strategy"] = overflow
        if sc.tool_selection_strategy is not None:
            overrides["tool_selection_strategy"] = sc.tool_selection_strategy
        mo: dict[str, Any] = {}
        if sc.thinking_level:
            tl, tb = map_business_depth(sc.thinking_level)
            mo["thinking_level"] = tl
            mo["thinking_budget"] = tb
        if sc.model_overrides:
            mo.update(sc.model_overrides)
        if mo:
            overrides["model_overrides"] = mo
        if sc.parallelism == "sequential":
            overrides["allow_parallel_tools"] = False
        elif sc.parallelism in ("safe-parallel", "full-parallel"):
            overrides["allow_parallel_tools"] = True
        if overrides:
            config = merge_agent_config(config, overrides)

    if config.risk_level is None and plan.risk_level:
        config = merge_agent_config(config, {"risk_level": plan.risk_level})
    return config


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall: TypeAlias = Callable[[str, str, str], None]
OnToolFinish: TypeAlias = OnToolFinishCallback
OnPlan: TypeAlias = Callable[[StructuredPlan], Awaitable[ConfirmationResult]]
OnThinking: TypeAlias = OnThinkingCallback


# 监控项中不计入 AgentRunResult.total_tool_calls / used_tools
_MONITOR_NON_TOOL_NAMES = frozenset({"llm_response"})


def _build_agent_run_result(reply: str, monitor: ToolMonitorProtocol) -> AgentRunResult:
    """从 monitor 汇总工具统计，构建 ``AgentRunResult``。"""
    all_stats = monitor.get_all_stats()
    tool_stats: dict[str, ToolStats] = {
        name: stats for name, stats in all_stats.items() if name not in _MONITOR_NON_TOOL_NAMES
    }
    used_tools = list(tool_stats.keys())
    total_tool_calls = sum(stats.calls for stats in tool_stats.values())
    return AgentRunResult(
        reply=reply,
        total_tool_calls=total_tool_calls,
        tool_stats=tool_stats,
        used_tools=used_tools,
    )


def _trace_agent_run(func: Any) -> Any:
    """Add an exception-safe end-to-end span while preserving the public signature."""

    @functools.wraps(func)
    async def wrapped(user_input: str, *args: Any, **kwargs: Any) -> AgentRunResult:
        from miniagent.agent.observability import emit_trace, new_trace_id, trace_parent

        session_key = str(kwargs.get("session_key") or "")
        span_id = new_trace_id("agent")
        wall_start = time.monotonic_ns()
        cpu_start = time.process_time_ns()
        emit_trace(
            {
                "type": "agent.run_start",
                "session_key": session_key,
                "span_id": span_id,
                "input_chars": len(user_input),
            }
        )
        success = False
        try:
            with trace_parent(span_id, session_key=session_key):
                result = await func(user_input, *args, **kwargs)
            success = True
            return result
        finally:
            emit_trace(
                {
                    "type": "agent.run_end",
                    "session_key": session_key,
                    "span_id": span_id,
                    "duration_ms": (time.monotonic_ns() - wall_start) / 1_000_000,
                    "cpu_ms": (time.process_time_ns() - cpu_start) / 1_000_000,
                    "success": success,
                }
            )

    return wrapped


# ─── 主入口 ──────────────────────────────────────────────


async def _execute_agent_plan(
    plan: StructuredPlan,
    user_input: str,
    *,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    config: AgentConfig,
    system_prompt: str | None,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    on_tool_call: OnToolCall | None,
    on_tool_finish: OnToolFinish | None,
    on_thinking: OnThinking | None,
    clawhub: Any | None,
    confirmation_channel: Any | None,
    tool_semaphore: asyncio.Semaphore | None,
    session_key: str | None,
) -> str:
    """执行计划，并在计划允许时以简化计划做一次有界降级。"""
    from miniagent.agent.observability import trace_span

    async def execute(current_plan: StructuredPlan, current_config: AgentConfig, phase: str) -> str:
        with trace_span(phase, session_key=session_key):
            return await execute_plan(
                current_plan,
                user_input,
                registry,
                monitor,
                current_config,
                on_tool_call,
                on_thinking,
                on_tool_finish=on_tool_finish,
                system_prompt=system_prompt,
                clawhub=clawhub,
                memory=memory,
                knowledge_registry=knowledge_registry,
                client=client,
                confirmation_channel=confirmation_channel,
                tool_semaphore=tool_semaphore,
                manage_activity_lifecycle=False,
            )

    reply = await execute(plan, config, "exec")
    if not (
        reply.startswith(WARNING_PREFIX) and plan.fallback_plan.degrade_to_simple and plan.steps
    ):
        return reply
    fallback_config = merge_agent_config(
        config,
        {"max_turns": plan.fallback_plan.degraded_max_turns},
    )
    simple_plan = StructuredPlan(
        summary=plan.summary,
        steps=[],
        required_toolboxes=plan.required_toolboxes,
        suggested_config=SuggestedConfig(max_turns=plan.fallback_plan.degraded_max_turns),
        estimated_tokens=plan.estimated_tokens,
        context_strategy=ContextStrategy(mode="normal", reason="fallback 降级"),
        risk_level=plan.risk_level,
        output_spec=plan.output_spec,
        fallback_plan=plan.fallback_plan,
        tools_enabled=plan.tools_enabled,
    )
    fallback_reply = await execute(simple_plan, fallback_config, "exec_fallback")
    return fallback_reply if not fallback_reply.startswith(WARNING_PREFIX) else reply


async def _reflect_agent_reply(
    user_input: str,
    reply: str,
    *,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    session_key: str | None,
    engine: Any | None,
    on_reflection: Callable[[Any], Any] | None = None,
) -> str:
    """按配置执行结果反思，并更新展示 footer 与引擎会话缓存。"""
    if not get_config("features.reflection", True):
        return reply
    from miniagent.agent.observability import trace_span

    with trace_span("reflect", session_key=session_key):
        reflection = await reflect_on_result(
            user_input,
            reply,
            knowledge_registry=knowledge_registry,
            client=client,
            on_thinking=None,
            session_key=session_key,
        )
    if engine is not None:
        key = session_key or "default"
        if not hasattr(engine, "_last_reflection") or not isinstance(engine._last_reflection, dict):
            engine._last_reflection = {}
        engine._last_reflection[key] = reflection
    if on_reflection is not None:
        callback_result = on_reflection(reflection)
        if isinstance(callback_result, Awaitable):
            await callback_result
    return reply + build_reflection_footer(reflection)


async def _prepare_control_stages(
    user_input: str,
    *,
    toolboxes: list[Toolbox],
    skip_planning: bool,
    clarifier: Any | None,
    confirmation_channel: Any | None,
    on_thinking: OnThinking | None,
    knowledge_registry: KnowledgeRegistryProtocol,
    memory: MemoryRuntimeProtocol,
    client: Any,
    config: AgentConfig,
    session_key: str | None,
) -> tuple[str, TaskDifficulty, bool]:
    """执行可选分类与澄清，返回增强输入、难度和有效跳过规划标志。"""
    from miniagent.agent.observability import trace_span

    difficulty = TaskDifficulty.NORMAL
    effective_skip = skip_planning
    if toolboxes and not skip_planning and task_classifier_enabled():
        with trace_span("classify", session_key=session_key):
            difficulty = await classify_task_difficulty(
                user_input,
                [toolbox.id for toolbox in toolboxes],
                knowledge_registry=knowledge_registry,
                client=client,
                agent_config=config,
            )
        effective_skip = difficulty == TaskDifficulty.SIMPLE
        if _announce_difficulty_and_plan_enabled() and on_thinking:
            await invoke_on_thinking(
                on_thinking,
                _format_task_difficulty(difficulty, display=True),
                True,
                PLANNING_STREAM_HEADER,
                full_record=_format_task_difficulty(difficulty),
            )
    if (
        not get_config("features.requirement_clarify", True)
        or clarifier is None
        or difficulty == TaskDifficulty.SIMPLE
    ):
        return user_input, difficulty, effective_skip
    clarified = await _clarify_user_input(
        user_input,
        difficulty=difficulty,
        clarifier=clarifier,
        confirmation_channel=confirmation_channel,
        on_thinking=on_thinking,
        knowledge_registry=knowledge_registry,
        memory=memory,
        client=client,
        session_key=session_key,
    )
    return clarified, difficulty, effective_skip


async def _clarify_user_input(
    user_input: str,
    *,
    difficulty: TaskDifficulty,
    clarifier: Any,
    confirmation_channel: Any | None,
    on_thinking: OnThinking | None,
    knowledge_registry: KnowledgeRegistryProtocol,
    memory: MemoryRuntimeProtocol,
    client: Any,
    session_key: str | None,
) -> str:
    """运行需求澄清；可选能力失败时保留原始输入继续。"""
    from miniagent.agent.observability import trace_span

    async def ask_user(question: str) -> str:
        if confirmation_channel is None or on_thinking is None:
            _logger.warning("需求澄清: confirmation_channel 或 on_thinking 未设置，跳过追问")
            return ""
        await invoke_on_thinking(on_thinking, f"❓ {question}", True, "[需求澄清]")
        request = ConfirmationRequest(stage=ConfirmationStage.CLARIFICATION, content=question)
        result = await confirmation_channel.request_confirmation(request)
        if result.rejected:
            return ""
        answer = (result.adjustment or "").strip()
        if answer:
            await invoke_on_thinking(
                on_thinking,
                f"用户回复：{answer}",
                True,
                "[需求澄清]",
            )
        return answer

    if on_thinking:
        try:
            await invoke_on_thinking(
                on_thinking,
                "正在分析需求，识别模糊表述与边界条件…",
                True,
                "[需求澄清]",
            )
        except Exception as error:
            _logger.debug("调用thinking回调失败: %s", error, exc_info=True)
    try:
        max_questions = min(
            _clarifier_max_questions_for_difficulty(difficulty),
            int(get_config("agent.max_questions", CLARIFIER_MAX_QUESTIONS_COMPLEX)),
        )
        with trace_span("clarify", session_key=session_key):
            clarified = await clarifier.clarify(
                user_input,
                ask_user=ask_user,
                client=client,
                on_thinking=on_thinking,
                memory_store=memory.store,
                knowledge_registry=knowledge_registry,
                session_key=session_key,
                max_questions=max_questions,
            )
        if not clarified:
            return user_input
        prompt = clarifier.to_system_prompt(clarified)
        if _announce_difficulty_and_plan_enabled() and on_thinking:
            await invoke_on_thinking(
                on_thinking,
                f"需求已澄清：{getattr(clarified, 'clarified_goal', '')[:80]}",
                True,
                "[需求澄清]",
                full_record=prompt,
            )
        return f"{user_input}\n\n{prompt}"
    except Exception as error:
        _logger.warning("需求澄清失败: %s", error, exc_info=True)
        return user_input


async def _prepare_plan(
    user_input: str,
    *,
    toolboxes: list[Toolbox],
    skip_planning: bool,
    difficulty: TaskDifficulty,
    config: AgentConfig,
    registry: ToolRegistryProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    on_plan: OnPlan | None,
    on_thinking: OnThinking | None,
    session_key: str | None,
) -> tuple[StructuredPlan | None, AgentConfig, bool, str | None]:
    """生成默认或 LLM 计划，并处理高风险计划确认/调整循环。"""
    from miniagent.agent.observability import trace_span

    if skip_planning or not toolboxes:
        plan = _create_default_plan(
            tools_enabled=bool(toolboxes) and not _user_forbids_tools(user_input)
        )
        if toolboxes and skip_planning and difficulty == TaskDifficulty.SIMPLE:
            config = merge_agent_config(
                config,
                {"model_overrides": exec_merge_for_simple_path()},
            )
        return plan, config, False, None
    plan_input = user_input
    rounds_left = EXECUTION_MAX_PLAN_CONFIRM_ROUNDS
    while True:
        with trace_span("plan", session_key=session_key):
            plan = await generate_plan(
                plan_input,
                toolboxes,
                config.log_file,
                client=client,
                agent_config=config,
                registry=registry,
                knowledge_registry=knowledge_registry,
                planner_model_overrides=planner_merge_for_difficulty(difficulty),
                default_step_thinking=default_step_thinking_for_difficulty(difficulty),
            )
        config = _merge_plan_suggested_config(plan, config)
        if not plan.requires_confirmation or on_plan is None:
            return plan, config, True, None
        if on_thinking:
            try:
                await invoke_on_thinking(
                    on_thinking,
                    f"{WARNING_PREFIX} 高风险操作，请确认执行计划。输入 /confirm 同意，/reject 拒绝，/adjust 调整。",
                    True,
                    "[等待确认]",
                )
            except Exception as error:
                _logger.debug("等待确认推送失败（非关键）: %s", error, exc_info=True)
        action, adjustment = (await on_plan(plan)).plan_action()
        if action == "cancel":
            return None, config, True, f"{WARNING_PREFIX} 操作已取消"
        if action != "replan":
            return plan, config, True, None
        rounds_left -= 1
        if rounds_left <= 0:
            return None, config, True, f"{WARNING_PREFIX} 计划调整次数过多，已取消"
        if adjustment:
            plan_input = f"{plan_input}\n\n[用户计划调整] {adjustment}"


def _merge_invocation_config(
    options: AgentRunOptions | None,
    agent_config: dict[str, Any] | None,
    session_key: str | None,
) -> AgentConfig:
    """按默认值、运行选项、显式覆盖的顺序合并一次调用配置。"""
    config = get_default_agent_config()
    overlay: dict[str, Any] = {}
    if options is not None and options.agent_config:
        overlay.update(options.agent_config)
    if options is not None and options.model_config:
        model_overrides = dict(overlay.get("model_overrides") or {})
        model_overrides.update(options.model_config)
        overlay["model_overrides"] = model_overrides
    if overlay:
        config = merge_agent_config(config, overlay)
    config = merge_agent_config(config, agent_config or {})
    requested_key = (session_key or "").strip() or None
    if requested_key and not config.session_config.session_key:
        config = merge_agent_config(
            config,
            {"session_config": {"session_key": requested_key}},
        )
    return config


@dataclass(slots=True)
class _AgentInvocation:
    """持有一次 Agent 编排的配置、会话日志和统计生命周期。"""

    user_input: str
    memory: MemoryRuntimeProtocol
    monitor: ToolMonitorProtocol
    toolboxes: list[Toolbox]
    config: AgentConfig
    system_prompt: str | None
    activity_enabled: bool

    @property
    def session_key(self) -> str | None:
        """返回归一化后的下游会话键。"""
        return self.config.session_config.session_key or None

    async def start(self) -> None:
        """为可持久化会话记录单一开始事件。"""
        if not self.activity_enabled or not self.session_key:
            return
        source = "feishu" if self.session_key.startswith("feishu:") else "cli"
        await invoke_activity_log(
            self.memory.activity_log,
            "log_session_start",
            self.session_key,
            self.user_input,
            source,
        )

    async def finish(self, reply: str) -> AgentRunResult:
        """记录最终回复并从监控器生成稳定结果 DTO。"""
        if self.activity_enabled and self.session_key:
            await invoke_activity_log(
                self.memory.activity_log,
                "log_final_reply",
                self.session_key,
                reply,
            )
        return _build_agent_run_result(reply, self.monitor)


def _create_agent_invocation(
    user_input: str,
    *,
    memory: MemoryRuntimeProtocol,
    monitor: ToolMonitorProtocol | None,
    toolboxes: list[Toolbox] | None,
    agent_config: dict[str, Any] | None,
    options: AgentRunOptions | None,
    system_prompt: str | None,
    session_key: str | None,
) -> _AgentInvocation:
    """解析一次公共调用的可选参数并建立生命周期对象。"""
    effective_monitor = monitor or DefaultToolMonitor()
    config = _merge_invocation_config(options, agent_config, session_key)
    effective_system = (
        system_prompt if system_prompt is not None else (options.system_prompt if options else None)
    )
    effective_key = config.session_config.session_key or None
    activity_enabled = False
    if effective_key:
        from miniagent.agent.session_keys import is_background_session_key

        activity_enabled = not is_background_session_key(effective_key)
    return _AgentInvocation(
        user_input,
        memory,
        effective_monitor,
        toolboxes or [],
        config,
        effective_system,
        activity_enabled,
    )


async def _announce_plan(
    plan: StructuredPlan,
    *,
    toolboxes: list[Toolbox],
    skip_planning: bool,
    difficulty: TaskDifficulty,
    from_llm_planner: bool,
    on_thinking: OnThinking | None,
) -> None:
    """重置规划展示段，避免澄清前后的难度信息重复。"""
    if not _announce_difficulty_and_plan_enabled() or on_thinking is None:
        return
    no_toolboxes = not toolboxes
    simple_classified = bool(toolboxes) and not skip_planning and difficulty == TaskDifficulty.SIMPLE
    common = {
        "from_llm_planner": from_llm_planner,
        "no_toolboxes": no_toolboxes,
        "user_skip_planning": skip_planning,
        "simple_classified": simple_classified,
    }
    await invoke_on_thinking(
        on_thinking,
        _format_plan_display_short(plan, **common),
        True,
        PLANNING_STREAM_HEADER,
        full_record=_format_plan_message(plan, **common),
        reset=True,
    )


@_trace_agent_run
async def run_agent(user_input: str, *,
    registry: ToolRegistryProtocol,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
    monitor: ToolMonitorProtocol | None = None,
    toolboxes: list[Toolbox] | None = None,
    agent_config: dict[str, Any] | None = None,
    options: AgentRunOptions | None = None,
    system_prompt: str | None = None,
    skip_planning: bool = False,
    on_tool_call: OnToolCall | None = None,
    on_tool_finish: OnToolFinish | None = None,
    on_plan: OnPlan | None = None,
    on_thinking: OnThinking | None = None,
    clawhub: Any | None = None,
    clarifier: Any | None = None,
    session_key: str | None = None,
    confirmation_channel: Any | None = None,
    engine: Any | None = None,
    on_reflection: Callable[[Any], Any] | None = None,
    tool_semaphore: asyncio.Semaphore | None = None,
) -> AgentRunResult:
    """运行分类、澄清、规划、确认、ReAct 执行和可选反思管线。"""
    invocation = _create_agent_invocation(
        user_input,
        memory=memory,
        monitor=monitor,
        toolboxes=toolboxes,
        agent_config=agent_config,
        options=options,
        system_prompt=system_prompt,
        session_key=session_key,
    )
    await invocation.start()
    controlled_input, difficulty, effective_skip = await _prepare_control_stages(
        invocation.user_input,
        toolboxes=invocation.toolboxes,
        skip_planning=skip_planning,
        clarifier=clarifier,
        confirmation_channel=confirmation_channel,
        on_thinking=on_thinking,
        knowledge_registry=knowledge_registry,
        memory=memory,
        client=client,
        config=invocation.config,
        session_key=invocation.session_key,
    )
    plan, execution_config, from_llm_planner, early_reply = await _prepare_plan(
        controlled_input,
        toolboxes=invocation.toolboxes,
        skip_planning=effective_skip,
        difficulty=difficulty,
        config=invocation.config,
        registry=registry,
        knowledge_registry=knowledge_registry,
        client=client,
        on_plan=on_plan,
        on_thinking=on_thinking,
        session_key=invocation.session_key,
    )
    if early_reply is not None:
        return await invocation.finish(early_reply)
    assert plan is not None
    await _announce_plan(
        plan,
        toolboxes=invocation.toolboxes,
        skip_planning=skip_planning,
        difficulty=difficulty,
        from_llm_planner=from_llm_planner,
        on_thinking=on_thinking,
    )
    reply = await _execute_agent_plan(
        plan,
        controlled_input,
        registry=registry,
        monitor=invocation.monitor,
        config=execution_config,
        system_prompt=invocation.system_prompt,
        memory=memory,
        knowledge_registry=knowledge_registry,
        client=client,
        on_tool_call=on_tool_call,
        on_tool_finish=on_tool_finish,
        on_thinking=on_thinking,
        clawhub=clawhub,
        confirmation_channel=confirmation_channel,
        tool_semaphore=tool_semaphore,
        session_key=invocation.session_key,
    )
    reply = await _reflect_agent_reply(
        controlled_input,
        reply,
        knowledge_registry=knowledge_registry,
        client=client,
        session_key=invocation.session_key,
        engine=engine,
        on_reflection=on_reflection,
    )
    return await invocation.finish(reply)


__all__ = ["run_agent", "run_pipeline", "PLANNING_STREAM_HEADER"]
