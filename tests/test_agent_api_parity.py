"""The object and function Agent APIs normalize into one internal turn model."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miniagent.agent.agent import run_agent
from miniagent.agent.runtime import AgentRequest, AgentRuntime, AgentSettings, AgentSpec
from miniagent.agent.types.agent import AgentRunResult


@pytest.mark.asyncio
async def test_agent_apis_share_the_normalized_turn_path(monkeypatch: pytest.MonkeyPatch) -> None:
    turns: list[object] = []

    async def fake_turn(turn: object) -> AgentRunResult:
        turns.append(turn)
        return AgentRunResult(reply="same")

    monkeypatch.setattr("miniagent.agent.agent._run_agent_turn", fake_turn)
    registry = MagicMock()
    memory = MagicMock()
    knowledge = MagicMock()
    llm = MagicMock()
    config = {"max_turns": 3}

    function_result = await run_agent(
        "question",
        registry=registry,
        memory=memory,
        knowledge_registry=knowledge,
        client=llm,
        toolboxes=[],
        agent_config=config,
        session_key="session-a",
    )
    runtime = AgentRuntime(
        AgentSpec(
            settings=AgentSettings({}),
            registry=registry,
            memory=memory,
            knowledge=knowledge,
            owns_llm=False,
            owns_memory=False,
        ),
        llm,
    )
    await runtime.start()
    object_result = await runtime.run(
        AgentRequest(
            "question",
            session_key="session-a",
            config=config,
        )
    )
    await runtime.stop()

    assert function_result == object_result == AgentRunResult(reply="same")
    assert len(turns) == 2
    first, second = turns
    for attribute in (
        "user_input",
        "registry",
        "memory",
        "knowledge_registry",
        "client",
        "toolboxes",
        "agent_config",
        "session_key",
    ):
        assert getattr(first, attribute) == getattr(second, attribute)
