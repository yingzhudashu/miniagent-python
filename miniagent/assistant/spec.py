"""Declarative specification for composing Assistant instances."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from miniagent.agent.lifecycle import LifecycleService
from miniagent.agent.runtime import AgentRuntime
from miniagent.ui.contracts import UIInput, UISurface

AgentFactory = Callable[[], AgentRuntime]
SurfaceFactory = Callable[[], UISurface]
ServiceFactory = Callable[[], LifecycleService]
CommandHandler = Callable[[UIInput, AgentRuntime], Awaitable[Any]]
ContainerFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class AssistantSpec:
    """Everything needed to construct one isolated Assistant application."""

    name: str
    agent_factory: AgentFactory | None = None
    surface_factories: tuple[SurfaceFactory, ...] = ()
    service_factories: tuple[ServiceFactory, ...] = ()
    command_handler: CommandHandler | None = None
    llm_config: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    agent_config: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    state_dir: str = "workspaces"
    container_factory: ContainerFactory | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("assistant name must not be empty")
        if self.agent_factory is None and self.container_factory is None:
            raise ValueError("assistant spec requires an agent or container factory")
        if self.agent_factory is not None and self.container_factory is not None:
            raise ValueError("assistant spec cannot use two composition strategies")
        object.__setattr__(self, "surface_factories", tuple(self.surface_factories))
        object.__setattr__(self, "service_factories", tuple(self.service_factories))
        object.__setattr__(self, "llm_config", MappingProxyType(dict(self.llm_config)))
        object.__setattr__(self, "agent_config", MappingProxyType(dict(self.agent_config)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class PersonalAssistantSpec(AssistantSpec):
    """Named specification type for the bundled personal-assistant recipe."""


__all__ = [
    "AssistantSpec",
    "CommandHandler",
    "PersonalAssistantSpec",
]
