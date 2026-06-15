"""Tests for AgentRunResult / AgentRunOptions integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miniagent.core.agent import _build_agent_run_result, run_agent
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.types.agent import AgentRunOptions, AgentRunResult


def test_build_agent_run_result_excludes_llm_response() -> None:
    monitor = DefaultToolMonitor()
    monitor.record("read_file", 10, success=True)
    monitor.record("read_file", 20, success=True)
    monitor.record("llm_response", 100, success=True)

    result = _build_agent_run_result("hello", monitor)

    assert isinstance(result, AgentRunResult)
    assert result.reply == "hello"
    assert result.total_tool_calls == 2
    assert result.used_tools == ["read_file"]
    assert "llm_response" not in result.tool_stats


@pytest.mark.asyncio
async def test_run_agent_returns_agent_run_result(tmp_path) -> None:
    from tests.config_helpers import install_test_config

    install_test_config(tmp_path, {"features": {"reflection": False}})

    with patch("miniagent.core.agent.get_default_agent_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            log_file=None,
            max_turns=10,
            debug=False,
            risk_level=None,
            loop_detection=None,
        )
        with patch("miniagent.core.agent.merge_agent_config", side_effect=lambda a, b: a):
            with patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False):
                with patch(
                    "miniagent.core.agent.execute_plan",
                    new_callable=AsyncMock,
                    return_value="done",
                ):
                    registry = MagicMock()
                    registry.get_schemas.return_value = []
                    registry.get_all.return_value = {}
                    registry.list.return_value = []

                    monitor = DefaultToolMonitor()
                    monitor.record("demo_tool", 5, success=True)

                    result = await run_agent(
                        "task",
                        registry=registry,
                        monitor=monitor,
                        skip_planning=True,
                    )

    assert isinstance(result, AgentRunResult)
    assert result.reply == "done"
    assert result.total_tool_calls == 1
    assert result.used_tools == ["demo_tool"]


@pytest.mark.asyncio
async def test_run_agent_options_merge_model_config(tmp_path) -> None:
    from tests.config_helpers import install_test_config

    install_test_config(tmp_path, {"features": {"reflection": False}})

    merge_calls: list[dict] = []

    def _capture_merge(base, overlay):
        merge_calls.append(dict(overlay))
        return base

    execute_kwargs: dict = {}

    async def _capture_execute(*args, **kwargs):
        execute_kwargs.update(kwargs)
        return "ok"

    with patch("miniagent.core.agent.get_default_agent_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            log_file=None,
            max_turns=10,
            debug=False,
            risk_level=None,
            loop_detection=None,
        )
        with patch("miniagent.core.agent.merge_agent_config", side_effect=_capture_merge):
            with patch("miniagent.core.constants.EXECUTION_TASK_CLASSIFIER_ENABLED", False):
                with patch("miniagent.core.agent.execute_plan", new_callable=AsyncMock) as mock_exec:
                    mock_exec.side_effect = _capture_execute
                    registry = MagicMock()
                    registry.get_schemas.return_value = []
                    registry.get_all.return_value = {}
                    registry.list.return_value = []

                    await run_agent(
                        "task",
                        registry=registry,
                        skip_planning=True,
                        options=AgentRunOptions(
                            model_config={"temperature": 0.2},
                            system_prompt="from-options",
                        ),
                        system_prompt="from-kwarg",
                        agent_config={"max_turns": 99},
                    )

    assert merge_calls[0]["model_overrides"]["temperature"] == 0.2
    assert merge_calls[1]["max_turns"] == 99
    assert execute_kwargs["system_prompt"] == "from-kwarg"
