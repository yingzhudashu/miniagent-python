"""Stable package-level API contracts for the 4.0 architecture."""

from __future__ import annotations

import inspect
from dataclasses import fields

import miniagent.agent as agent_package
from miniagent.agent import AgentRequest, AgentRuntime, AgentSpec
from miniagent.assistant import (
    AssistantApplication,
    AssistantSpec,
    create_assistant,
    create_assistant_application,
    create_personal_assistant,
    run_assistant,
)


def _parameter_names(callable_: object) -> list[str]:
    return list(inspect.signature(callable_).parameters)


def test_agent_v4_public_signatures_are_stable() -> None:
    assert _parameter_names(AgentRuntime.run) == [
        "self",
        "request",
        "run_id",
        "trace_id",
    ]
    assert _parameter_names(AgentRuntime.cancel) == ["self", "run_id"]
    assert [field.name for field in fields(AgentRequest)] == [
        "user_input",
        "session_key",
        "toolboxes",
        "system_prompt",
        "options",
        "config",
        "skip_planning",
        "attachments",
        "metadata",
        "idempotency_key",
        "trace_id",
    ]
    assert [field.name for field in fields(AgentSpec)] == [
        "settings",
        "registry",
        "memory",
        "knowledge",
        "monitor",
        "observer",
        "clawhub",
        "clarifier",
        "confirmation_channel",
        "tool_semaphore",
        "runner",
        "max_parallel_sessions",
        "shutdown_timeout",
        "owns_llm",
        "owns_memory",
    ]


def test_v3_agent_facade_is_not_a_package_level_api() -> None:
    assert not hasattr(agent_package, "Agent")
    assert not hasattr(agent_package, "AgentServices")
    assert not hasattr(agent_package, "run_agent")


def test_assistant_v4_public_signatures_are_stable() -> None:
    assert _parameter_names(AssistantApplication) == ["container"]
    assert _parameter_names(create_assistant) == ["spec"]
    assert _parameter_names(create_assistant_application) == []
    assert _parameter_names(create_personal_assistant) == []
    assert _parameter_names(run_assistant) == ["argv"]
    assert [field.name for field in fields(AssistantSpec)] == [
        "name",
        "agent_factory",
        "surface_factories",
        "service_factories",
        "command_handler",
        "llm_config",
        "agent_config",
        "metadata",
        "state_dir",
        "container_factory",
    ]
