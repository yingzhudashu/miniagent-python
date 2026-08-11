"""Exact package-level API contracts for the 5.0 architecture."""

from __future__ import annotations

import inspect
from dataclasses import fields

import miniagent.agent as agent_package
import miniagent.assistant as assistant_package
from miniagent.agent import AgentRequest, AgentRuntime, AgentSpec
from miniagent.assistant import (
    AssistantSpec,
    PersonalAssistantApplication,
    create_assistant,
    create_personal_assistant,
    run_assistant,
)


def _parameter_names(callable_: object) -> list[str]:
    return list(inspect.signature(callable_).parameters)


def test_agent_public_signatures_are_current() -> None:
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
        "engine",
        "tool_semaphore",
        "runner",
        "max_parallel_sessions",
        "shutdown_timeout",
        "owns_llm",
        "owns_memory",
    ]


def test_removed_agent_facades_are_not_public() -> None:
    assert not hasattr(agent_package, "Agent")
    assert not hasattr(agent_package, "AgentServices")
    assert not hasattr(agent_package, "run_agent")
    import miniagent.agent.agent as agent_module

    assert not hasattr(agent_module, "run_agent")


def test_assistant_public_signatures_are_current() -> None:
    assert _parameter_names(PersonalAssistantApplication) == ["container"]
    assert _parameter_names(create_assistant) == ["spec"]
    assert _parameter_names(create_personal_assistant) == []
    assert _parameter_names(run_assistant) == ["argv"]
    assert [field.name for field in fields(AssistantSpec)] == [
        "name",
        "agent_factory",
        "surface_factories",
        "service_factories",
        "command_handler",
    ]
    for removed in (
        "AssistantApplication",
        "PersonalAssistantSpec",
        "personal_assistant_spec",
        "create_assistant_application",
    ):
        assert not hasattr(assistant_package, removed)


def test_personal_assistant_factory_uses_current_container_factory(
    monkeypatch,
) -> None:
    container = object()
    monkeypatch.setattr(
        "miniagent.assistant.bootstrap.entrypoint.create_application_container",
        lambda: container,
    )
    application = create_personal_assistant()
    assert isinstance(application, PersonalAssistantApplication)
    assert application.container is container
