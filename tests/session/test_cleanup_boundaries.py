"""Focused regressions migrated from test_recovery_edge_matrix.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import bg_session_cleanup


@pytest.mark.asyncio
async def test_cleanup_optional_agent_trace_and_lock_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.assistant.engine.session_lock.release_session_lock", MagicMock(side_effect=RuntimeError)
    )
    await bg_session_cleanup._release_background_session_lock("__bg__x")

    monkeypatch.setattr(
        "miniagent.assistant.memory.layered_memory.remove_agent_longterm_entries_for_session",
        MagicMock(side_effect=RuntimeError),
    )
    await bg_session_cleanup._remove_background_agent_memory("__bg__x")
    monkeypatch.setattr(
        bg_session_cleanup, "_remove_session_trace_events", AsyncMock(return_value=2)
    )
    await bg_session_cleanup._remove_background_traces("__bg__x")
