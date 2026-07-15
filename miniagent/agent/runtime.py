"""Stable object-oriented facade for the high-quality answer pipeline."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from miniagent.agent.agent import run_agent
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.types.agent import AgentRunOptions, AgentRunResult, ToolMonitorProtocol
from miniagent.agent.types.confirmation import ConfirmationResult
from miniagent.agent.types.planning import StructuredPlan
from miniagent.agent.types.tool import Toolbox, ToolRegistryProtocol

AgentResult = AgentRunResult


@runtime_checkable
class AgentObserver(Protocol):
    """Receives semantic progress events without coupling Agent to a UI."""

    async def on_thinking(
        self,
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None: ...

    def on_tool_call(self, name: str, arguments: str, result: str) -> None: ...

    async def on_tool_finish(
        self,
        name: str,
        arguments: str,
        result: str,
        success: bool,
        *,
        thinking_header: str | None = None,
    ) -> None: ...

    async def on_plan(self, plan: StructuredPlan) -> ConfirmationResult: ...

    async def on_reflection(self, reflection: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentServices:
    """Injected capabilities required by an Agent instance."""

    llm: Any
    registry: ToolRegistryProtocol
    memory: MemoryRuntimeProtocol
    knowledge: KnowledgeRegistryProtocol
    monitor: ToolMonitorProtocol | None = None
    observer: AgentObserver | None = None
    clawhub: Any | None = None
    clarifier: Any | None = None
    confirmation_channel: Any | None = None
    tool_semaphore: asyncio.Semaphore | None = None
    runner: Any | None = None


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """Immutable input for one complete classify-to-reflect answer turn."""

    user_input: str
    session_key: str | None = None
    toolboxes: tuple[Toolbox, ...] = ()
    system_prompt: str | None = None
    options: AgentRunOptions | None = None
    config: dict[str, Any] | None = None
    skip_planning: bool = False


def _method(observer: AgentObserver | None, name: str) -> Any | None:
    value = getattr(observer, name, None) if observer is not None else None
    return value if callable(value) else None


class Agent:
    """Reusable Agent entry point; it owns no channel, session, or storage state."""

    def __init__(self, services: AgentServices) -> None:
        self._services = services

    async def run(self, request: AgentRequest) -> AgentResult:
        observer = self._services.observer
        on_reflection = _method(observer, "on_reflection")

        async def reflection_callback(value: Any) -> None:
            if on_reflection is None:
                return
            result = on_reflection(value)
            if inspect.isawaitable(result):
                await result

        runner = self._services.runner or run_agent
        return await runner(
            request.user_input,
            registry=self._services.registry,
            memory=self._services.memory,
            knowledge_registry=self._services.knowledge,
            client=self._services.llm,
            monitor=self._services.monitor,
            toolboxes=list(request.toolboxes),
            agent_config=request.config,
            options=request.options,
            system_prompt=request.system_prompt,
            skip_planning=request.skip_planning,
            on_tool_call=_method(observer, "on_tool_call"),
            on_tool_finish=_method(observer, "on_tool_finish"),
            on_plan=_method(observer, "on_plan"),
            on_thinking=_method(observer, "on_thinking"),
            on_reflection=reflection_callback if on_reflection is not None else None,
            clawhub=self._services.clawhub,
            clarifier=self._services.clarifier,
            session_key=request.session_key,
            confirmation_channel=self._services.confirmation_channel,
            tool_semaphore=self._services.tool_semaphore,
        )


__all__ = ["Agent", "AgentObserver", "AgentRequest", "AgentResult", "AgentServices"]
