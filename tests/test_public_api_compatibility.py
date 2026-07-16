"""Stable package-level API contracts for the 3.0 architecture."""

from __future__ import annotations

import inspect
from dataclasses import fields

from miniagent.agent import Agent, AgentRequest, AgentServices, run_agent
from miniagent.assistant import (
    AssistantApplication,
    create_assistant_application,
    run_assistant,
)
from miniagent.assistant.bootstrap.application import ApplicationContainer


def _parameter_names(callable_: object) -> list[str]:
    return list(inspect.signature(callable_).parameters)


def test_agent_public_signatures_remain_compatible() -> None:
    assert _parameter_names(Agent.run) == ["self", "request"]
    assert _parameter_names(run_agent) == [
        "user_input",
        "registry",
        "memory",
        "knowledge_registry",
        "client",
        "monitor",
        "toolboxes",
        "agent_config",
        "options",
        "system_prompt",
        "skip_planning",
        "on_tool_call",
        "on_tool_finish",
        "on_plan",
        "on_thinking",
        "clawhub",
        "clarifier",
        "session_key",
        "confirmation_channel",
        "engine",
        "on_reflection",
        "tool_semaphore",
    ]
    assert [field.name for field in fields(AgentRequest)] == [
        "user_input",
        "session_key",
        "toolboxes",
        "system_prompt",
        "options",
        "config",
        "skip_planning",
    ]
    assert [field.name for field in fields(AgentServices)] == [
        "llm",
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
    ]


def test_assistant_public_signatures_remain_compatible() -> None:
    assert _parameter_names(AssistantApplication) == ["container"]
    assert _parameter_names(create_assistant_application) == []
    assert _parameter_names(run_assistant) == ["argv"]
    assert [field.name for field in fields(ApplicationContainer)] == [
        "registry",
        "monitor",
        "skill_registry",
        "clawhub",
        "engine",
        "channel_router",
        "message_queue",
        "feishu",
        "memory",
        "knowledge_registry",
        "background_tasks",
        "config",
        "outbound_channels",
        "cli_outbound_dispatcher",
        "lifecycle_manager",
        "llm_gateway",
        "retired_llm_gateways",
        "create_feishu_handler_factory",
        "cli_transcript_append_ansi",
        "cli_transcript_append",
        "cli_transcript_coordinator",
        "shutdown_tracked_tasks",
    ]
