"""Focused regressions migrated from test_cleanup_and_optimizer_edge_matrix.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.engine import bg_session_cleanup


@pytest.mark.asyncio
async def test_cleanup_collaborator_failures_are_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class BrokenManager:
        def forget_session(self, _key: str) -> None:
            raise RuntimeError("manager")

    broken = MagicMock(side_effect=RuntimeError("memory"))
    memory = SimpleNamespace(
        state_root=str(tmp_path),
        store=SimpleNamespace(evict_session=broken),
        remove_session_entries=broken,
        activity_log=SimpleNamespace(remove_session=AsyncMock(side_effect=RuntimeError("log"))),
    )
    monkeypatch.setattr(bg_session_cleanup, "_release_background_session_lock", AsyncMock())
    monkeypatch.setattr(bg_session_cleanup, "_remove_background_agent_memory", AsyncMock())
    monkeypatch.setattr(bg_session_cleanup, "_remove_background_traces", AsyncMock())

    await bg_session_cleanup.cleanup_background_session_artifacts(
        "__bg__broken", session_manager=BrokenManager(), memory=memory
    )

    assert broken.call_count == 2
    memory.activity_log.remove_session.assert_awaited_once()

@pytest.mark.asyncio
async def test_cleanup_helpers_swallow_optional_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bg_session_cleanup,
        "_remove_session_trace_events",
        AsyncMock(side_effect=RuntimeError("trace")),
    )
    await bg_session_cleanup._remove_background_traces("__bg__x")

    memory = SimpleNamespace(
        store=SimpleNamespace(evict_session=None),
        activity_log=SimpleNamespace(remove_session=None),
        remove_session_entries=MagicMock(),
    )
    await bg_session_cleanup._remove_background_memory_entries("__bg__x", memory)
    await bg_session_cleanup._remove_background_activity_log("__bg__x", memory)
    await bg_session_cleanup._forget_background_session("__bg__x", None)
