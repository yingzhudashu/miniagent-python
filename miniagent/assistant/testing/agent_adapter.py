"""Adapt ``AgentRuntime`` to the self-test execution contract."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from miniagent.agent.runtime import AgentRequest, AgentRuntime, AgentSpec
from miniagent.agent.settings import AgentSettings
from miniagent.assistant.infrastructure.json_config import get_config_snapshot
from miniagent.assistant.testing.types import AgentExecutionResult, ExecuteAgentFn
from miniagent.assistant.testing.validation import build_agent_execution_dict, estimate_token_count


async def _run_test_agent(
    user_input: str,
    *,
    registry: Any,
    memory: Any,
    knowledge_registry: Any,
    client: Any,
    monitor: Any,
    toolboxes: list[Any],
    system_prompt: str | None,
    agent_config: dict[str, Any],
    session_key: str,
    on_tool_finish: Any,
    engine: Any = None,
):
    """Run one self-test request through the current ``AgentRuntime`` contract."""
    runtime = AgentRuntime(
        AgentSpec(
            settings=AgentSettings(get_config_snapshot()),
            registry=registry,
            memory=memory,
            knowledge=knowledge_registry,
            monitor=monitor,
            observer=SimpleNamespace(on_tool_finish=on_tool_finish),
            engine=engine,
            owns_llm=False,
            owns_memory=False,
        ),
        client,
    )
    await runtime.start()
    try:
        return await runtime.run(
            AgentRequest(
                user_input=user_input,
                session_key=session_key,
                toolboxes=tuple(toolboxes),
                system_prompt=system_prompt,
                config=agent_config,
            )
        )
    finally:
        await runtime.stop()


def build_execute_agent(
    *,
    registry: Any,
    memory: Any,
    knowledge_registry: Any,
    client: Any,
    skill_toolboxes: list | None = None,
    skill_prompts: str | None = None,
    session_key: str = "__self_test__",
    agent_config: dict[str, Any] | None = None,
) -> ExecuteAgentFn:
    """构建真实 Agent 执行函数。

    Args:
        registry: 工具注册表
        memory: 应用记忆运行时
        knowledge_registry: 应用知识库注册表
        skill_toolboxes: 技能工具箱列表
        skill_prompts: 技能系统提示词
        session_key: 隔离用的会话键（避免污染用户会话历史时可专用）
        agent_config: 合并进 ``AgentRequest.config`` 的配置覆盖

    Returns:
        符合 :class:`ExecuteAgentFn` 的异步 callable
    """
    toolboxes = skill_toolboxes or []
    base_config: dict[str, Any] = {
        "session_key": session_key,
        "debug": False,
    }
    if agent_config:
        base_config.update(agent_config)

    async def execute_agent(user_input: str, *, capture_tools: bool = True) -> AgentExecutionResult:
        from miniagent.agent.monitor import DefaultToolMonitor

        monitor = DefaultToolMonitor()
        captured_calls: list[dict[str, Any]] = []

        async def on_tool_finish(
            name: str,
            args_json: str,
            result: str,
            success: bool,
            **kwargs: Any,
        ) -> None:
            if capture_tools:
                captured_calls.append(
                    {"name": name, "args": args_json, "success": success}
                )

        result = await _run_test_agent(
            user_input,
            registry=registry,
            memory=memory,
            knowledge_registry=knowledge_registry,
            client=client,
            monitor=monitor,
            toolboxes=toolboxes,
            system_prompt=skill_prompts,
            agent_config=base_config,
            session_key=session_key,
            on_tool_finish=on_tool_finish,
        )

        reply = result.reply
        if captured_calls:
            tool_calls = captured_calls
        else:
            tool_calls = [{"name": name} for name, stats in result.tool_stats.items() for _ in range(stats.calls)]

        token_count = estimate_token_count(reply, len(tool_calls))
        return build_agent_execution_dict(
            reply=reply,
            tool_calls=tool_calls,
            token_count=token_count,
        )

    return execute_agent


async def build_execute_agent_from_engine(
    engine: Any,
    *,
    registry: Any,
    monitor: Any | None = None,
    skill_toolboxes: list | None = None,
    skill_prompts: str | None = None,
    state: dict[str, Any] | None = None,
    session_key: str = "__self_test__",
) -> ExecuteAgentFn:
    """从 AssistantTurnService 上下文构建 execute_agent（供 CLI ``/test run real`` 使用）。"""
    sm = (state or {}).get("session_manager")
    runtime_ctx = (state or {}).get("runtime_ctx")
    memory = getattr(runtime_ctx, "memory", None)
    knowledge_registry = getattr(runtime_ctx, "knowledge_registry", None)
    client = getattr(runtime_ctx, "llm_client", getattr(runtime_ctx, "llm_gateway", None))
    if memory is None:
        raise ValueError("真实 Agent 自测需要 state.runtime_ctx.memory")
    if knowledge_registry is None:
        raise ValueError("真实 Agent 自测需要 state.runtime_ctx.knowledge_registry")
    if client is None:
        raise ValueError("真实 Agent 自测需要 state.runtime_ctx.llm_client")
    if sm is not None:
        sm.get_or_create(session_key)

    toolboxes = skill_toolboxes or []

    async def execute_agent(user_input: str, *, capture_tools: bool = True) -> AgentExecutionResult:
        from miniagent.agent.monitor import DefaultToolMonitor

        run_monitor = DefaultToolMonitor()
        captured_calls: list[dict[str, Any]] = []

        async def on_tool_finish(
            name: str,
            args_json: str,
            result: str,
            success: bool,
            **kwargs: Any,
        ) -> None:
            if capture_tools:
                captured_calls.append(
                    {"name": name, "args": args_json, "success": success}
                )

        agent_config: dict[str, Any] = {"session_key": session_key, "debug": False}

        result = await _run_test_agent(
            user_input,
            registry=registry,
            memory=memory,
            knowledge_registry=knowledge_registry,
            client=client,
            monitor=run_monitor,
            toolboxes=toolboxes,
            system_prompt=skill_prompts,
            agent_config=agent_config,
            session_key=session_key,
            on_tool_finish=on_tool_finish,
            engine=engine,
        )

        reply = result.reply
        tool_calls = captured_calls or [
            {"name": name} for name, stats in result.tool_stats.items() for _ in range(stats.calls)
        ]
        return build_agent_execution_dict(reply=reply, tool_calls=tool_calls)

    return execute_agent


__all__ = [
    "build_execute_agent",
    "build_execute_agent_from_engine",
]
