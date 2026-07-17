"""Extension contract for optional Agent capabilities such as RAG and MCP."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from miniagent.agent.lifecycle import LifecycleService


@runtime_checkable
class AgentExtension(LifecycleService, Protocol):
    """A lifecycle-owned capability installed into exactly one AgentRuntime."""

    @property
    def extension_id(self) -> str:
        """Stable identifier used by configuration and diagnostics."""
        ...


__all__ = ["AgentExtension"]
