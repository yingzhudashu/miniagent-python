"""Mini Agent Python — 自测 Agent 执行适配器

将 :func:`miniagent.core.agent.run_agent` 适配为自测框架所需的
``execute_agent(user_input, capture_tools=True)`` 接口。
"""

from __future__ import annotations

from typing import Any

from miniagent.testing.types import AgentExecutionResult, ExecuteAgentFn
from miniagent.testing.validation import build_agent_execution_dict, estimate_token_count


def build_execute_agent(
    *,
    registry: Any,
    skill_toolboxes: list | None = None,
    skill_prompts: str | None = None,
    session_key: str = "__self_test__",
    agent_config: dict[str, Any] | None = None,
) -> ExecuteAgentFn:
    """构建真实 Agent 执行函数。

    Args:
        registry: 工具注册表
        skill_toolboxes: 技能工具箱列表
        skill_prompts: 技能系统提示词
        session_key: 隔离用的会话键（避免污染用户会话历史时可专用）
        agent_config: 合并进 run_agent 的配置覆盖

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
        from miniagent.core.agent import run_agent
        from miniagent.infrastructure.monitor import DefaultToolMonitor

        monitor = DefaultToolMonitor()
        captured_calls: list[dict[str, Any]] = []

        def on_tool_finish(
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

        result = await run_agent(
            user_input,
            registry=registry,
            monitor=monitor,
            toolboxes=toolboxes,
            system_prompt=skill_prompts,
            agent_config=base_config,
            session_key=session_key,
            on_tool_finish=on_tool_finish,
            skip_planning=False,
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
    """从 UnifiedEngine 上下文构建 execute_agent（供 CLI ``/test run real`` 使用）。"""
    sm = (state or {}).get("session_manager")
    if sm is not None:
        await sm.get_or_create(session_key)

    toolboxes = skill_toolboxes or []

    async def execute_agent(user_input: str, *, capture_tools: bool = True) -> AgentExecutionResult:
        from miniagent.core.agent import run_agent
        from miniagent.infrastructure.monitor import DefaultToolMonitor

        run_monitor = DefaultToolMonitor()
        captured_calls: list[dict[str, Any]] = []

        def on_tool_finish(
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

        result = await run_agent(
            user_input,
            registry=registry,
            monitor=run_monitor,
            toolboxes=toolboxes,
            system_prompt=skill_prompts,
            agent_config=agent_config,
            session_key=session_key,
            on_tool_finish=on_tool_finish,
            engine=engine,
            memory_store=getattr(engine, "memory_store", None),
            activity_log=getattr(engine, "activity_log", None),
            skip_planning=False,
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
