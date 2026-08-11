"""Current ``AgentRuntime`` adapters used by the self-test subsystem."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from miniagent.agent.types.agent import AgentRunResult, ToolStats
from miniagent.assistant.testing import agent_adapter


@pytest.mark.asyncio
async def test_runtime_adapter_owns_start_run_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Runtime:
        def __init__(self, spec: Any, client: Any) -> None:
            assert spec.owns_llm is False
            assert spec.owns_memory is False
            assert client == "llm"

        async def start(self) -> None:
            events.append("start")

        async def run(self, request: Any) -> AgentRunResult:
            events.append(f"run:{request.user_input}:{request.session_key}")
            return AgentRunResult(reply="answer")

        async def stop(self) -> None:
            events.append("stop")

    monkeypatch.setattr(agent_adapter, "AgentRuntime", Runtime)
    result = await agent_adapter._run_test_agent(
        "question",
        registry=MagicMock(),
        memory=MagicMock(),
        knowledge_registry=MagicMock(),
        client="llm",
        monitor=MagicMock(),
        toolboxes=[],
        system_prompt=None,
        agent_config={},
        session_key="self-test",
        on_tool_finish=MagicMock(),
    )
    assert result.reply == "answer"
    assert events == ["start", "run:question:self-test", "stop"]


@pytest.mark.asyncio
async def test_both_execute_factories_use_current_agent_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def run(_user_input: str, **kwargs: Any) -> AgentRunResult:
        calls.append(kwargs)
        await kwargs["on_tool_finish"]("read_file", "{}", "ok", True)
        return AgentRunResult(
            reply="answer",
            tool_stats={"read_file": ToolStats(calls=1)},
        )

    monkeypatch.setattr(agent_adapter, "_run_test_agent", run)
    direct = agent_adapter.build_execute_agent(
        registry=MagicMock(),
        memory=MagicMock(),
        knowledge_registry=MagicMock(),
        client=MagicMock(),
        agent_config={"skip_planning": True},
    )
    assert (await direct("question"))["tool_calls"] == [
        {"name": "read_file", "args": "{}", "success": True}
    ]

    session_manager = MagicMock()
    runtime_ctx = SimpleNamespace(
        memory=MagicMock(),
        knowledge_registry=MagicMock(),
        llm_client=MagicMock(),
    )
    from_engine = await agent_adapter.build_execute_agent_from_engine(
        MagicMock(),
        registry=MagicMock(),
        state={"session_manager": session_manager, "runtime_ctx": runtime_ctx},
    )
    assert (await from_engine("question"))["output"] == "answer"
    session_manager.get_or_create.assert_called_once_with("__self_test__")
    assert len(calls) == 2
