"""Immutable application configuration snapshot contract."""

from __future__ import annotations

from miniagent.agent.settings import AgentSettings


class ConfigSnapshot(AgentSettings):
    """Process configuration tree using the Agent's immutable mapping semantics."""


__all__ = ["ConfigSnapshot"]
