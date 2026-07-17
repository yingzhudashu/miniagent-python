"""Focused regressions migrated from test_final_diff_coverage_matrix.py."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.engine.turn_service import _turn_label_sort_key, _TurnThinkingRecorder

schedule_tools = importlib.import_module("miniagent.assistant.tools.schedule_tools")

@pytest.mark.asyncio
async def test_turn_recorder_sort_reset_concat_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    display = SimpleNamespace(show=AsyncMock())
    recorder = _TurnThinkingRecorder(display=display, session_key="s")
    await recorder.on_thinking("old", True, "[执行]")
    await recorder.on_thinking("different", True, "[执行]")
    await recorder.on_thinking("new", True, "[执行]", reset=True)
    await recorder.on_tool_finish("read", "{}", "ok", True, thinking_header="[步骤 1/1]")
    blob = recorder.history_blob()
    assert "new" in blob and "read" in blob
    assert _turn_label_sort_key(("[步骤 2/3] x", ""))[0] == 0
    assert _turn_label_sort_key(("[评估与计划]", ""))[0] == 1
    assert _turn_label_sort_key(("[执行]", ""))[0] == 2
    assert _turn_label_sort_key(("[第 3 轮]", ""))[0] == 3
    assert _turn_label_sort_key(("other", ""))[0] == 4
