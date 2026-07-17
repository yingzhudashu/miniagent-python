"""Focused regressions migrated from test_final_diff_coverage_matrix.py."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import command_dispatch

schedule_tools = importlib.import_module("miniagent.assistant.tools.schedule_tools")

@pytest.mark.asyncio
async def test_review_iterative_update_and_missing_improvement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miniagent.agent.llm_json as llm_module

    responses = iter(
        [
            {"has_issues": True, "issues": [{"description": "first"}], "improved_answer": "v1"},
            {"has_issues": True, "issues": [{"description": "second"}], "improved_answer": "v2"},
            {"has_issues": False, "issues": []},
            {"has_issues": True, "issues": [{"description": "first"}], "improved_answer": "v1"},
            {"has_issues": True, "issues": [{"description": "still"}]},
        ]
    )

    async def fake(**_kwargs):
        return next(responses)

    monkeypatch.setattr(llm_module, "llm_json", fake)
    assert "v2" in (await command_dispatch._run_review("q", "a", capture=True) or "")
    assert "v1" in (await command_dispatch._run_review("q", "a", capture=True) or "")

@pytest.mark.asyncio
async def test_self_test_real_builder_and_non_capture_return(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.assistant.testing.agent_adapter as adapter
    import miniagent.assistant.testing.test_runner as runner

    monkeypatch.setattr(adapter, "build_execute_agent_from_engine", AsyncMock(return_value="agent"))
    monkeypatch.setattr(
        runner,
        "run_self_test",
        AsyncMock(return_value=SimpleNamespace(passed=1, total=1, pass_rate=1.0, failed=0,
                                               skipped=0, duration_seconds=0.0, results=[])),
    )
    result = await command_dispatch._run_test(
        mock=False, registry=object(), capture=False, term_write=MagicMock()
    )
    assert result == ""
