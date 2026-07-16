"""Mini Agent Python — ReAct 循环执行器（两阶段中的执行阶段）

执行 Phase 1 产出的结构化计划，实现 ReAct 循环（Think → Act → Observe）。

工作流程：
1. 根据 plan.requiredToolboxes 筛选工具
2. 初始化循环检测器 / 上下文管理器
3. 构建 prompt 分层：stable system → history → current turn user context
4. ReAct 循环：LLM 调用 → 工具执行 → 结果反馈
5. 循环直到：LLM 不再调用工具 / 达到 maxTurns / 循环检测拦截

Internal 常量 ``PHASED_EXECUTION`` 开启且 ``plan.steps`` 非空时，按步骤分子循环（每步独立 thinking 解析）；
若最后一步单步子轮次用尽而全局 ``agent.max_turns`` 仍有余量，会追加一轮不传 tools 的收尾 synthesis。
详见 ``docs/ARCHITECTURE.md``。

**工具结果回注**：每轮工具输出经 ``tool`` role 消息写回 ``DefaultContextManager``；同轮 ``merge_tools``（若配置开启）可在展示层合并多工具行，但**不影响**此处消息序列语义。

**不变量**：工具调用均在 :class:`miniagent.agent.types.tool.ToolContext` 限定的 ``cwd`` / ``allowed_paths`` 内执行
（通常由沙箱默认工作区推导）。上下文 token 超预算时抛出
:class:`miniagent.agent.context.ContextBudgetExceeded`，由上层决定是否换会话或压缩。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeAlias

from miniagent.agent.constants import (
    EXECUTION_MAX_CONCURRENT_TOOLS,
    EXECUTION_PHASED_ENABLED,
    EXECUTION_STEP_MAX_TURNS,
    EXECUTION_THINKING_SEPARATOR,
    EXECUTION_TOOL_INTENT_IN_THINKING,
    EXECUTION_TOOL_INTENT_MAX_CHARS,
    MAX_ARGS_LOG_LEN,
)
from miniagent.agent.context import ContextBudgetExceeded, DefaultContextManager
from miniagent.agent.execution_prompts import (
    build_current_turn_user_context,
    build_stable_execution_system_prompt,
)
from miniagent.agent.logging import get_logger
from miniagent.agent.loop_detector import LoopDetector
from miniagent.agent.plan_utils import resolve_execution_step_groups
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.ports.runtime import (
    OnThinkingCallback,
    OnToolFinishCallback,
)
from miniagent.agent.prompts.identity import AGENT_IDENTITY
from miniagent.agent.settings import get_config
from miniagent.agent.thinking_presets import map_business_depth
from miniagent.agent.types.agent import LoopDetectionConfig, ToolMonitorProtocol
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.skill import ClawHubClientProtocol
from miniagent.agent.types.tool import ToolRegistryProtocol
from miniagent.llm.gateway import LLMGateway

_logger = get_logger(__name__)
_EXEC_LLM_MAX_ATTEMPTS = 3
_TOOL_INTENT_MAP: dict[str, str] = {
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "list_dir": "列出目录",
    "exec_command": "执行命令",
    "web_search": "搜索网页",
    "browser_extract_text": "浏览器提取正文",
    "fetch_url": "抓取网页",
    "read_memory": "读取记忆",
    "write_memory": "写入记忆",
    "search_memory": "搜索记忆",
    "git_status": "Git 状态",
    "git_diff": "Git 差异",
}


def _exec_retry_params(base: dict[str, Any], *, attempt: int, responses: bool) -> dict[str, Any]:
    """Keep the first execution request intact and adapt Responses retries only."""
    params = dict(base)
    if not responses or attempt == 0:
        return params
    params.pop("temperature", None)
    params.pop("top_p", None)
    params["_omit_parameters"] = ("temperature", "top_p")
    if attempt == _EXEC_LLM_MAX_ATTEMPTS - 1:
        params["_thinking_level"] = "medium"
    return params


def _raise_if_task_cancelled() -> None:
    """在 ReAct 循环内协作式响应 asyncio 任务取消。"""
    task = asyncio.current_task()
    if task is not None and task.cancelled():
        raise asyncio.CancelledError()


def _is_ephemeral_session(session_key: str | None) -> bool:
    """后台子 session（``__bg__*``）不落盘记忆/活动日志。"""
    from miniagent.agent.session_keys import is_background_session_key

    return is_background_session_key(session_key or "")


# ─── 工具错误日志辅助 ────────────────────────────────────────────

_MAX_ARGS_LOG_LEN = MAX_ARGS_LOG_LEN


@lru_cache(maxsize=1)
def _env_phased_execution_enabled() -> bool:
    """是否启用分阶段执行（工具批次与 LLM 轮次分段），默认开启。"""
    return EXECUTION_PHASED_ENABLED


@lru_cache(maxsize=1)
def _tool_intent_in_thinking_enabled() -> bool:
    """是否在工具执行前向 on_thinking 推送 🔧 意图行。"""
    return EXECUTION_TOOL_INTENT_IN_THINKING


@lru_cache(maxsize=1)
def _step_max_turns_cap() -> int:
    """分步模式下单步内 ReAct 轮数上限（默认 48）。"""
    return EXECUTION_STEP_MAX_TURNS


@lru_cache(maxsize=1)
def _thinking_segment_separator() -> str:
    """同一步内多轮 LLM 思考片段拼接符；默认双换行。"""
    raw = EXECUTION_THINKING_SEPARATOR
    if raw:
        return raw.replace("\\n", "\n")
    return "\n\n"


@lru_cache(maxsize=1)
def _tool_intent_max_chars() -> int:
    """工具意图摘要写入思考流时的最大字符数。"""
    return EXECUTION_TOOL_INTENT_MAX_CHARS


def _reset_env_caches_for_tests() -> None:
    """重置环境变量缓存（仅供测试使用）。"""
    _env_phased_execution_enabled.cache_clear()
    _tool_intent_in_thinking_enabled.cache_clear()
    _step_max_turns_cap.cache_clear()
    _thinking_segment_separator.cache_clear()
    _tool_intent_max_chars.cache_clear()


from miniagent.agent.execution_runtime_setup import (
    resolve_exec_tools as _resolve_exec_tools,
)
from miniagent.agent.execution_runtime_setup import (
    step_thinking_header as _step_thinking_header,
)


def _append_context_or_return(
    context_manager: DefaultContextManager,
    msg: dict[str, Any],
) -> str | None:
    """追加消息；``error`` 溢出策略触发时返回用户可操作警告。"""
    try:
        context_manager.append(msg)
    except ContextBudgetExceeded as e:
        return f"{WARNING_PREFIX} {e}"
    return None


def _resolve_feishu_receive_id_type(raw: str) -> str | None:
    """规范化飞书接收 ID 类型，非法配置回落到全局默认值。"""
    allowed = {"chat_id", "open_id", "union_id"}
    normalized = raw.strip().lower()
    if normalized not in allowed:
        normalized = str(get_config("feishu.receive_id_type", "chat_id"))
    return normalized if normalized in allowed else None


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]  # (name, args_json, result)
# 使用 Protocol 类型替代 Callable[..., Any]，详见 miniagent/types/protocols.py
OnThinking: TypeAlias = (
    OnThinkingCallback  # (text, streaming, header, *, full_record=..., reset=...)
)
OnToolFinish: TypeAlias = (
    OnToolFinishCallback  # (name, args_json, result, success, *, thinking_header=...)
)


# ─── 核心：execute_plan（ReAct 主循环；可选分步子循环 + 无 tools 收尾 synthesis）──


async def _finish_text_turn(
    final_reply: str,
    start_ms: int,
    *,
    context_manager: DefaultContextManager,
    monitor: ToolMonitorProtocol,
    persist_memory: Callable[[str], Awaitable[None]],
    save_memory: bool,
    debug: bool,
) -> str | None:
    """提交无工具的 assistant 文本，并按需持久化会话记忆。"""
    monitor.record("llm_response", time.monotonic_ns() // 1_000_000 - start_ms, True)
    out_of_budget = _append_context_or_return(
        context_manager,
        {"role": "assistant", "content": final_reply},
    )
    if out_of_budget:
        return out_of_budget
    if save_memory:
        await persist_memory(final_reply)
    if debug:
        _logger.debug(context_manager.get_token_report())
    return None


async def _run_unphased_execution(
    *,
    turns_left: int,
    tools: list[Any],
    turn_streamer: Any,
    tool_runner: Any,
    context_manager: DefaultContextManager,
    monitor: ToolMonitorProtocol,
    persist_memory: Callable[[str], Awaitable[None]],
    debug: bool,
) -> str | None:
    """运行传统整体 ReAct 循环；耗尽轮次时返回 ``None``。"""
    while turns_left > 0:
        _raise_if_task_cancelled()
        turns_left -= 1
        message, _, start_ms, _, _, label = await turn_streamer.stream_exec_turn(
            None,
            tools,
            "[执行]",
            is_last_step=True,
        )
        if message.tool_calls:
            early = await tool_runner.run_tool_calls_phase(message, start_ms, label)
            if early is not None:
                return early
            continue
        final_reply = message.content or "(空回复)"
        out_of_budget = await _finish_text_turn(
            final_reply,
            start_ms,
            context_manager=context_manager,
            monitor=monitor,
            persist_memory=persist_memory,
            save_memory=True,
            debug=debug,
        )
        return out_of_budget or final_reply
    return None


def _phased_step_hint(step: Any, index: int, total: int) -> str:
    """构建单个执行步骤的动态用户提示。"""
    return (
        f"[执行步骤 {step.step_number or index + 1}/{total}] {step.description}\n"
        f"预期输入：{step.expected_input}\n"
        f"预期产出：{step.expected_output}\n"
        "请仅完成本步骤；若当前无需工具，请直接给出简短步骤小结。"
    )


async def _run_phased_step(
    *,
    step: Any,
    index: int,
    total: int,
    turns_left: int,
    plan: StructuredPlan,
    registry: ToolRegistryProtocol,
    agent_config: AgentConfig,
    turn_streamer: Any,
    tool_runner: Any,
    context_manager: DefaultContextManager,
    monitor: ToolMonitorProtocol,
    persist_memory: Callable[[str], Awaitable[None]],
) -> tuple[str | None, int, bool]:
    """运行一个分步子循环，返回结果、剩余轮次和是否自然结束。"""
    is_last = index + 1 >= total
    label = _step_thinking_header(index, total, step)
    tools = _resolve_exec_tools(registry, agent_config, plan, step)
    context_manager.set_tools(tools)
    out_of_budget = _append_context_or_return(
        context_manager,
        {"role": "user", "content": _phased_step_hint(step, index, total)},
    )
    if out_of_budget:
        return out_of_budget, turns_left, False
    step_left = min(_step_max_turns_cap(), turns_left)
    thinking_level, thinking_budget = map_business_depth(step.thinking_level)
    overrides = {"thinking_level": thinking_level, "thinking_budget": thinking_budget}
    while step_left > 0 and turns_left > 0:
        _raise_if_task_cancelled()
        turns_left -= 1
        step_left -= 1
        message, _, start_ms, _, _, turn_label = await turn_streamer.stream_exec_turn(
            overrides,
            tools,
            label,
            is_last_step=is_last,
        )
        if message.tool_calls:
            early = await tool_runner.run_tool_calls_phase(message, start_ms, turn_label)
            if early is not None:
                return early, turns_left, False
            continue
        final_reply = message.content or "(空回复)"
        out_of_budget = await _finish_text_turn(
            final_reply,
            start_ms,
            context_manager=context_manager,
            monitor=monitor,
            persist_memory=persist_memory,
            save_memory=is_last,
            debug=agent_config.debug,
        )
        return out_of_budget or (final_reply if is_last else None), turns_left, True
    if is_last:
        return await _synthesize_last_phased_step(
            turns_left=turns_left,
            overrides=overrides,
            label=label,
            turn_streamer=turn_streamer,
            context_manager=context_manager,
            monitor=monitor,
            persist_memory=persist_memory,
            debug=agent_config.debug,
        )
    return None, turns_left, False


async def _synthesize_last_phased_step(
    *,
    turns_left: int,
    overrides: dict[str, Any],
    label: str,
    turn_streamer: Any,
    context_manager: DefaultContextManager,
    monitor: ToolMonitorProtocol,
    persist_memory: Callable[[str], Awaitable[None]],
    debug: bool,
) -> tuple[str, int, bool]:
    """最后一步工具轮次耗尽时，用剩余全局轮次执行一次无工具收尾。"""
    if turns_left <= 0:
        return _phased_limit_message(), turns_left, False
    out_of_budget = _append_context_or_return(
        context_manager,
        {
            "role": "user",
            "content": (
                "（系统：本步单步子轮次已用尽；工具结果已在上下文中。"
                "请仅用自然语言给出本步的最终简短小结，不要调用工具。）"
            ),
        },
    )
    if out_of_budget:
        return out_of_budget, turns_left, False
    turns_left -= 1
    message, _, start_ms, _, _, _ = await turn_streamer.stream_exec_turn(
        overrides,
        [],
        label,
        is_last_step=True,
    )
    if message.tool_calls:
        return _phased_limit_message(), turns_left, False
    final_reply = message.content or "(空回复)"
    error = await _finish_text_turn(
        final_reply,
        start_ms,
        context_manager=context_manager,
        monitor=monitor,
        persist_memory=persist_memory,
        save_memory=True,
        debug=debug,
    )
    return error or final_reply, turns_left, True


def _phased_limit_message() -> str:
    """返回最后一步未自然结束时的可操作提示。"""
    return (
        f"{WARNING_PREFIX} 最后一步在单步子轮次（Internal EXECUTION_STEP_MAX_TURNS）或总轮数限制内，"
        "未以「无工具调用」形式结束。\n\n"
        "可在 config.user.json 提高 agent.max_turns，"
        "或联系维护者调整 Internal 分步执行常量（EXECUTION_PHASED_ENABLED / EXECUTION_STEP_MAX_TURNS）后重试。"
    )


async def _run_phased_execution(
    *,
    groups: list[Any],
    turns_left: int,
    plan: StructuredPlan,
    registry: ToolRegistryProtocol,
    agent_config: AgentConfig,
    turn_streamer: Any,
    tool_runner: Any,
    context_manager: DefaultContextManager,
    monitor: ToolMonitorProtocol,
    persist_memory: Callable[[str], Awaitable[None]],
) -> str | None:
    """按计划分组顺序运行步骤；耗尽全局轮次时返回 ``None``。"""
    total = sum(len(steps) for _, steps in groups)
    index = 0
    for chunk_prompt, steps in groups:
        prompt = (chunk_prompt or "").strip()
        if prompt:
            error = _append_context_or_return(
                context_manager,
                {"role": "user", "content": f"## 分块执行上下文\n{prompt}"},
            )
            if error:
                return error
        for step in steps:
            result, turns_left, resolved = await _run_phased_step(
                step=step,
                index=index,
                total=total,
                turns_left=turns_left,
                plan=plan,
                registry=registry,
                agent_config=agent_config,
                turn_streamer=turn_streamer,
                tool_runner=tool_runner,
                context_manager=context_manager,
                monitor=monitor,
                persist_memory=persist_memory,
            )
            index += 1
            if result is not None:
                return result
            if not resolved and turns_left > 0:
                error = _append_context_or_return(
                    context_manager,
                    {
                        "role": "user",
                        "content": (
                            "（系统提示：上一步在单步子轮次内未结束，以下继续下一步；"
                            "若结果不理想可适当提高 agent.max_turns 或 Internal EXECUTION_STEP_MAX_TURNS。）"
                        ),
                    },
                )
                if error:
                    return error
    return None


def _build_tool_context(
    agent_config: AgentConfig,
    *,
    clawhub: ClawHubClientProtocol | None,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: Any,
) -> Any:
    """构造受工作区 allowlist 约束的工具上下文。"""
    from miniagent.agent.execution_runtime_setup import build_tool_context

    return build_tool_context(
        agent_config,
        clawhub=clawhub,
        knowledge_registry=knowledge_registry,
        client=client,
        receive_id_type_resolver=_resolve_feishu_receive_id_type,
    )


def _build_loop_detector(agent_config: AgentConfig) -> tuple[LoopDetector, LoopDetectionConfig]:
    """从强类型配置或兼容字典构造本轮循环检测器。"""
    from miniagent.agent.execution_runtime_setup import build_loop_detector

    return build_loop_detector(agent_config)


async def _build_execution_context(
    plan: StructuredPlan,
    user_input: str,
    *,
    tools: list[dict[str, Any]],
    agent_config: AgentConfig,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    system_prompt: str | None,
    client: LLMGateway,
) -> tuple[DefaultContextManager, bool, bool]:
    """注入本轮记忆与知识，并按稳定前缀顺序恢复会话历史。"""
    from miniagent.agent.execution_runtime_setup import build_execution_context

    return await build_execution_context(
        plan,
        user_input,
        tools=tools,
        agent_config=agent_config,
        memory=memory,
        knowledge_registry=knowledge_registry,
        system_prompt=system_prompt,
        ephemeral_resolver=_is_ephemeral_session,
        llm_gateway=client,
    )


@dataclass(slots=True)
class _ExecutionRuntime:
    """持有一次 ReAct 执行的资源所有权与活动日志生命周期。"""

    plan: StructuredPlan
    user_input: str
    registry: ToolRegistryProtocol
    monitor: ToolMonitorProtocol
    config: AgentConfig
    memory: MemoryRuntimeProtocol
    context: DefaultContextManager
    loop_detector: LoopDetector
    loop_config: LoopDetectionConfig
    tools: list[dict[str, Any]]
    turn_streamer: Any
    tool_runner: Any
    ephemeral: bool
    activity_enabled: bool
    manage_activity: bool
    turn_tool_calls: list[dict[str, Any]]

    @property
    def session_key(self) -> str:
        """返回用于 Trace 与活动日志的稳定会话键。"""
        return self.config.session_config.session_key or "default"

    async def start(self) -> None:
        """记录直接执行入口的会话开始事件并输出调试摘要。"""
        if self.manage_activity and self.activity_enabled:
            source = "feishu" if self.session_key.startswith("feishu:") else "cli"
            await self.memory.activity_log.log_session_start(
                self.session_key,
                self.user_input,
                source,
            )
        if self.config.debug:
            stats = self.memory.keyword_index.get_stats()
            _logger.info("使用 %d 个工具 (策略: %s)", len(self.tools), self.config.tool_selection_strategy)
            _logger.info("计划: %s", self.plan.summary)
            _logger.info(
                "最大轮数: %d | 循环检测: %s",
                self.config.max_turns,
                "启用" if self.loop_config.enabled else "禁用",
            )
            _logger.debug("三层记忆: L3(关键词索引 %d 词)", stats["total_keywords"])

    async def persist(self, final_reply: str) -> None:
        """成功回合后写入记忆；后台临时会话始终跳过持久化。"""
        session_key = self.config.session_config.session_key
        if not session_key or not final_reply or self.ephemeral:
            return
        from miniagent.agent.observability import trace_span

        with trace_span("memory_persist", session_key=self.session_key):
            await self.memory.context.save_memory_after_turn(
                session_key,
                self.user_input,
                final_reply,
                self.memory.store,
                tool_calls=self.turn_tool_calls,
            )
        if self.manage_activity:
            await self.memory.activity_log.log_final_reply(self.session_key, final_reply)

    async def run(self) -> str:
        """选择分步或普通路径，并统一处理最大轮次耗尽。"""
        await self.start()
        groups = resolve_execution_step_groups(self.plan)
        if _env_phased_execution_enabled() and groups:
            result = await _run_phased_execution(
                groups=groups,
                turns_left=self.config.max_turns,
                plan=self.plan,
                registry=self.registry,
                agent_config=self.config,
                turn_streamer=self.turn_streamer,
                tool_runner=self.tool_runner,
                context_manager=self.context,
                monitor=self.monitor,
                persist_memory=self.persist,
            )
        else:
            result = await _run_unphased_execution(
                turns_left=self.config.max_turns,
                tools=self.tools,
                turn_streamer=self.turn_streamer,
                tool_runner=self.tool_runner,
                context_manager=self.context,
                monitor=self.monitor,
                persist_memory=self.persist,
                debug=self.config.debug,
            )
        if result is not None:
            return result
        if self.manage_activity and self.activity_enabled:
            await self.memory.activity_log.log_incomplete(
                self.session_key,
                f"达到最大轮数 {self.config.max_turns}",
            )
        if self.config.debug:
            _logger.debug(self.context.get_token_report())
        calls = self.loop_detector.get_stats()["total_calls"]
        return (
            f"{WARNING_PREFIX} 达到最大调用次数（{self.config.max_turns} 轮），任务未完成。\n\n"
            f"建议：简化请求，分步骤执行。\n\n📊 本轮统计：工具调用 {calls} 次"
        )


async def _create_execution_runtime(
    plan: StructuredPlan,
    user_input: str,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    agent_config: AgentConfig,
    *,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    client: LLMGateway,
    system_prompt: str | None,
    clawhub: ClawHubClientProtocol | None,
    on_tool_call: OnToolCall | None,
    on_tool_finish: OnToolFinish | None,
    on_thinking: OnThinking | None,
    confirmation_channel: Any | None,
    tool_semaphore: asyncio.Semaphore | None,
    manage_activity_lifecycle: bool,
) -> _ExecutionRuntime:
    """装配一次执行所需的上下文、流式器、工具运行器和所有者对象。"""
    from miniagent.agent.execution_tools import ToolPhaseRunner
    from miniagent.agent.execution_turn import ExecutionTurnStreamer

    effective_registry = agent_config.session_config.session_registry or registry
    tools = _resolve_exec_tools(effective_registry, agent_config, plan, None)
    context, ephemeral, activity_enabled = await _build_execution_context(
        plan,
        user_input,
        tools=tools,
        agent_config=agent_config,
        memory=memory,
        knowledge_registry=knowledge_registry,
        system_prompt=system_prompt,
        client=client,
    )
    loop_detector, loop_config = _build_loop_detector(agent_config)
    session_key = agent_config.session_config.session_key or "default"
    turn_tool_calls: list[dict[str, Any]] = []
    turn_streamer = ExecutionTurnStreamer(
        context_manager=context,
        agent_config=agent_config,
        on_thinking=on_thinking,
        phase_header_sent=set(),
        session_key=session_key,
        llm_client=client,
        exec_hist_segments={},
        activity_log_enabled=activity_enabled,
        activity_log=memory.activity_log,
        separator=_thinking_segment_separator(),
    )
    tool_runner = ToolPhaseRunner(
        context_manager=context,
        agent_config=agent_config,
        effective_registry=effective_registry,
        session_key=session_key,
        on_tool_call=on_tool_call,
        loop_detector=loop_detector,
        monitor=monitor,
        turn_tool_calls=turn_tool_calls,
        activity_log_enabled=activity_enabled,
        activity_log=memory.activity_log,
        confirmation_channel=confirmation_channel,
        on_thinking=on_thinking,
        tool_context=_build_tool_context(
            agent_config,
            clawhub=clawhub,
            knowledge_registry=knowledge_registry,
            client=client,
        ),
        execution_semaphore=tool_semaphore
        or asyncio.Semaphore(max(1, min(20, EXECUTION_MAX_CONCURRENT_TOOLS))),
        on_tool_finish=on_tool_finish,
        loop_warning_shown=False,
    )
    return _ExecutionRuntime(
        plan,
        user_input,
        effective_registry,
        monitor,
        agent_config,
        memory,
        context,
        loop_detector,
        loop_config,
        tools,
        turn_streamer,
        tool_runner,
        ephemeral,
        activity_enabled,
        manage_activity_lifecycle,
        turn_tool_calls,
    )


async def execute_plan(
    plan: StructuredPlan,
    user_input: str,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol,
    agent_config: AgentConfig,
    on_tool_call: OnToolCall | None = None,
    on_thinking: OnThinking | None = None,
    *,
    memory: MemoryRuntimeProtocol,
    knowledge_registry: KnowledgeRegistryProtocol,
    on_tool_finish: OnToolFinish | None = None,
    system_prompt: str | None = None,
    clawhub: ClawHubClientProtocol | None = None,
    client: Any,
    confirmation_channel: Any | None = None,
    tool_semaphore: asyncio.Semaphore | None = None,
    manage_activity_lifecycle: bool = True,
) -> str:
    """装配并执行结构化计划的 ReAct 循环。

    所有工具调用受 :class:`ToolContext` 路径 allowlist 约束。上下文按稳定系统提示、
    历史、本轮动态上下文的顺序装配；后台临时会话不写活动日志或长期记忆。
    """
    runtime = await _create_execution_runtime(
        plan,
        user_input,
        registry,
        monitor,
        agent_config,
        memory=memory,
        knowledge_registry=knowledge_registry,
        client=client,
        system_prompt=system_prompt,
        clawhub=clawhub,
        on_tool_call=on_tool_call,
        on_tool_finish=on_tool_finish,
        on_thinking=on_thinking,
        confirmation_channel=confirmation_channel,
        tool_semaphore=tool_semaphore,
        manage_activity_lifecycle=manage_activity_lifecycle,
    )
    return await runtime.run()


# ─── 工具意图提取 ────────────────────────────────────────────


def _extract_tool_intent(tool_name: str, args: dict[str, Any]) -> str:
    """从工具名称与关键参数生成有界意图摘要。"""
    base = _TOOL_INTENT_MAP.get(tool_name, f"调用 {tool_name}")
    for key in ("path", "query", "command", "content", "url"):
        if key not in args:
            continue
        value = str(args[key])
        cap = _tool_intent_max_chars()
        if cap > 0 and len(value) > cap:
            value = value[:cap] + f"…（共 {len(value)} 字）"
        return f"{base}: {value}"
    return base


__all__ = [
    "execute_plan",
    "AGENT_IDENTITY",
    "build_stable_execution_system_prompt",
    "build_current_turn_user_context",
]
