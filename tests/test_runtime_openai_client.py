"""ApplicationContainer client injection through the engine execution path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from miniagent.agent.types.agent import AgentRunResult
from miniagent.assistant.engine.engine import UnifiedEngine
from tests.memory_helpers import make_knowledge_registry, make_memory_runtime


@pytest.mark.asyncio
async def test_run_agent_with_thinking_forwards_client_to_run_agent() -> None:
    captured: dict = {}

    async def fake_run_agent(*_a, **kwargs: object) -> AgentRunResult:
        captured["client"] = kwargs.get("client")
        return AgentRunResult(reply="ok")

    fake_llm = MagicMock(name="injected_llm")

    sm = MagicMock()
    sess = MagicMock()
    sess.conversation_history = []
    sm.get_or_create = MagicMock(return_value=sess)

    with patch("miniagent.assistant.engine.engine.run_agent", new=fake_run_agent):
        engine = UnifiedEngine()
        await engine.run_agent_with_thinking(
            "hello",
            "session-a",
            [],
            None,
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            registry=MagicMock(),
            monitor=MagicMock(),
            session_manager=sm,
            client=fake_llm,
        )

    assert captured.get("client") is fake_llm


@pytest.mark.asyncio
async def test_run_agent_with_thinking_requires_session_manager() -> None:
    engine = UnifiedEngine()
    with pytest.raises(ValueError, match="session_manager"):
        await engine.run_agent_with_thinking(
            "hello",
            "session-a",
            [],
            None,
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            client=MagicMock(),
            registry=MagicMock(),
            monitor=MagicMock(),
            session_manager=None,
        )


@pytest.mark.asyncio
async def test_run_agent_forwards_client_to_execute_plan(tmp_path) -> None:
    """run_agent 传入的 client 应传入 execute_plan（空 toolboxes 走默认计划，不调用规划 LLM）。"""
    from miniagent.agent.agent import run_agent
    from miniagent.agent.monitor import DefaultToolMonitor
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
    from tests.config_helpers import install_test_config

    install_test_config(tmp_path, {"features": {"reflection": False}})

    called: dict = {}

    async def fake_execute_plan(
        *_a: object,
        client: object = None,
        **kwargs: object,
    ) -> str:
        called["client"] = client
        return "done"

    fake = MagicMock(name="llm")
    with patch("miniagent.agent.agent.execute_plan", new=fake_execute_plan):
        reg = DefaultToolRegistry()
        mon = DefaultToolMonitor()
        await run_agent(
            "u",
            registry=reg,
            memory=make_memory_runtime(),
            knowledge_registry=make_knowledge_registry(),
            monitor=mon,
            toolboxes=[],
            client=fake,
            agent_config={
                "session_config": {"session_key": "k"},
                "max_turns": 1,
                "debug": False,
            },
        )

    assert called.get("client") is fake
