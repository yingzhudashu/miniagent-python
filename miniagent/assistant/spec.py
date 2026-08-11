"""Declarative specification for composing Assistant instances."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from miniagent.agent.lifecycle import LifecycleService
from miniagent.agent.runtime import AgentRuntime
from miniagent.ui.contracts import UIInput, UISurface

AgentFactory = Callable[[], AgentRuntime]
SurfaceFactory = Callable[[], UISurface]
ServiceFactory = Callable[[], LifecycleService]
CommandHandler = Callable[[UIInput, AgentRuntime], Awaitable[Any]]
@dataclass(frozen=True, slots=True)
class AssistantSpec:
    """Everything needed to construct one isolated Assistant application."""

    name: str
    agent_factory: AgentFactory
    surface_factories: tuple[SurfaceFactory, ...] = ()
    service_factories: tuple[ServiceFactory, ...] = ()
    command_handler: CommandHandler | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("assistant name must not be empty")
        object.__setattr__(self, "surface_factories", tuple(self.surface_factories))
        object.__setattr__(self, "service_factories", tuple(self.service_factories))


__all__ = [
    "AssistantSpec",
    "CommandHandler",
]
