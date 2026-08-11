"""Test helper that exercises the public AgentRuntime contract directly."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from miniagent.agent.runtime import AgentRequest, AgentRuntime, AgentSpec
from miniagent.agent.settings import AgentSettings
from miniagent.agent.types.agent import AgentRunOptions, AgentRunResult
from miniagent.assistant.infrastructure.json_config import get_config_snapshot


async def run_agent(
    user_input: str,
    *,
    registry: Any,
    memory: Any,
    knowledge_registry: Any,
    client: Any,
    monitor: Any = None,
    toolboxes: list[Any] | None = None,
    agent_config: dict[str, Any] | None = None,
    options: AgentRunOptions | None = None,
    system_prompt: str | None = None,
    skip_planning: bool = False,
    on_tool_call: Any = None,
    on_tool_finish: Any = None,
    on_plan: Any = None,
    on_thinking: Any = None,
    clawhub: Any = None,
    clarifier: Any = None,
    session_key: str | None = None,
    confirmation_channel: Any = None,
    engine: Any = None,
    on_reflection: Any = None,
    tool_semaphore: asyncio.Semaphore | None = None,
) -> AgentRunResult:
    callbacks = {
        name: callback
        for name, callback in {
            "on_tool_call": on_tool_call,
            "on_tool_finish": on_tool_finish,
            "on_plan": on_plan,
            "on_thinking": on_thinking,
            "on_reflection": on_reflection,
        }.items()
        if callback is not None
    }
    runtime = AgentRuntime(
        AgentSpec(
            settings=AgentSettings(get_config_snapshot()),
            registry=registry,
            memory=memory,
            knowledge=knowledge_registry,
            monitor=monitor,
            observer=SimpleNamespace(**callbacks),
            clawhub=clawhub,
            clarifier=clarifier,
            confirmation_channel=confirmation_channel,
            engine=engine,
            tool_semaphore=tool_semaphore,
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
                toolboxes=tuple(toolboxes or ()),
                system_prompt=system_prompt,
                options=options,
                config=agent_config,
                skip_planning=skip_planning,
            )
        )
    finally:
        await runtime.stop()


__all__ = ["run_agent"]
