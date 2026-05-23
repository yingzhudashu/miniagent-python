"""RuntimeContext + UnifiedEngine 对 LLM 客户端的贯通。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from miniagent.engine.engine import UnifiedEngine


@pytest.mark.asyncio
async def test_run_agent_with_thinking_forwards_client_to_run_agent() -> None:
    captured: dict = {}

    async def fake_run_agent(*_a, **kwargs: object) -> str:
        captured["client"] = kwargs.get("client")
        return "ok"

    fake_llm = MagicMock(name="injected_llm")

    sm = MagicMock()
    sess = MagicMock()
    sess.conversation_history = []
    sm.get_or_create = MagicMock(return_value=sess)

    with patch("miniagent.engine.engine.run_agent", new=fake_run_agent):
        engine = UnifiedEngine()
        await engine.run_agent_with_thinking(
            "hello",
            "session-a",
            [],
            None,
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
            registry=MagicMock(),
            monitor=MagicMock(),
            session_manager=None,
        )


@pytest.mark.asyncio
async def test_run_agent_forwards_client_to_execute_plan() -> None:
    """run_agent 传入的 client 应传入 execute_plan（空 toolboxes 走默认计划，不调用规划 LLM）。"""
    import os
    from miniagent.core.agent import run_agent
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry

    called: dict = {}

    async def fake_execute_plan(
        *_a: object,
        client: object = None,
        **kwargs: object,
    ) -> str:
        called["client"] = client
        return "done"

    fake = MagicMock(name="llm")
    with patch.dict(os.environ, {"MINIAGENT_REFLECTION": "0"}):
        with patch("miniagent.core.agent.execute_plan", new=fake_execute_plan):
            reg = DefaultToolRegistry()
            mon = DefaultToolMonitor()
            await run_agent(
                "u",
                registry=reg,
                monitor=mon,
                toolboxes=[],
                client=fake,
                agent_config={"session_key": "k", "max_turns": 1, "debug": False},
            )

    assert called.get("client") is fake
