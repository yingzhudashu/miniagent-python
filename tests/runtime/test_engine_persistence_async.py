"""Engine history persistence scheduling tests."""

from __future__ import annotations

import pytest

from miniagent.assistant.engine.turn_service import _persist_session_history


@pytest.mark.asyncio
async def test_persist_session_history_prefers_manager_async_method() -> None:
    calls: list[str] = []

    class Manager:
        async def save_session_history_async(self, session_key: str) -> None:
            calls.append(session_key)

    await _persist_session_history(Manager(), "session-a")

    assert calls == ["session-a"]


@pytest.mark.asyncio
async def test_missing_async_persistence_contract_is_rejected() -> None:
    class InvalidManager:
        pass

    with pytest.raises(AttributeError):
        await _persist_session_history(InvalidManager(), "session-b")
