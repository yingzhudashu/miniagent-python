"""Engine history persistence scheduling tests."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from miniagent.engine.engine import _persist_session_history


@pytest.mark.asyncio
async def test_persist_session_history_prefers_manager_async_method() -> None:
    calls: list[str] = []

    class Manager:
        async def save_session_history_async(self, session_key: str) -> None:
            calls.append(session_key)

        def save_session_history(self, session_key: str) -> None:
            raise AssertionError("sync persistence should not be used")

    await _persist_session_history(Manager(), "session-a")

    assert calls == ["session-a"]


@pytest.mark.asyncio
async def test_sync_persistence_fallback_runs_off_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    heartbeat = asyncio.Event()

    class LegacyManager:
        def save_session_history(self, session_key: str) -> None:
            assert session_key == "session-b"
            worker_threads.append(threading.get_ident())
            time.sleep(0.03)

    async def pulse() -> None:
        await asyncio.sleep(0.005)
        heartbeat.set()

    pulse_task = asyncio.create_task(pulse())
    await _persist_session_history(LegacyManager(), "session-b")
    await pulse_task

    assert heartbeat.is_set()
    assert worker_threads and worker_threads[0] != loop_thread
