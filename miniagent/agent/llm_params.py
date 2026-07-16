"""Build provider-neutral request overrides for Agent completion phases."""

from __future__ import annotations

from typing import Any

from miniagent.agent.types.config import AgentConfig
from miniagent.llm.types import LLMRole

_REQUEST_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "max_tokens",
        "service_tier",
        "thinking_level",
        "thinking_budget",
        "extra_body",
    }
)


def _overrides(
    agent_config: AgentConfig | None,
    merge_overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    values = dict(agent_config.llm_overrides) if agent_config else {}
    if merge_overrides:
        values.update(merge_overrides)
    return values


def resolve_request_profile(
    agent_config: AgentConfig | None,
    role: LLMRole,
    *,
    merge_overrides: dict[str, Any] | None = None,
) -> str | None:
    """Resolve an optional explicit profile without accepting raw model ids."""
    values = _overrides(agent_config, merge_overrides)
    value = values.get(f"{role}_profile", values.get("profile"))
    normalized = str(value or "").strip()
    return normalized or None


def resolve_exec_completion_kwargs(
    agent_config: AgentConfig,
    *,
    stream: bool,
    merge_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return execution overrides; stream selection belongs to the Gateway call."""
    del stream
    values = _overrides(agent_config, merge_overrides)
    result = {key: values[key] for key in _REQUEST_KEYS if key in values}
    for public, internal in (
        ("thinking_level", "_thinking_level"),
        ("thinking_budget", "_thinking_budget"),
    ):
        if public in result:
            result[internal] = result.pop(public)
    return result


def resolve_planner_completion_kwargs(
    agent_config: AgentConfig | None,
    *,
    merge_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return stable planning defaults merged with explicit current overrides."""
    values = _overrides(agent_config, merge_overrides)
    result: dict[str, Any] = {
        "temperature": float(values.get("planner_temperature", values.get("temperature", 0.3))),
        "max_tokens": int(values.get("planner_max_tokens", 2048)),
    }
    for key in ("top_p", "service_tier", "extra_body"):
        if key in values:
            result[key] = values[key]
    if "thinking_level" in values:
        result["_thinking_level"] = values["thinking_level"]
    if "thinking_budget" in values:
        result["_thinking_budget"] = values["thinking_budget"]
    return result


__all__ = [
    "resolve_exec_completion_kwargs",
    "resolve_planner_completion_kwargs",
    "resolve_request_profile",
]
