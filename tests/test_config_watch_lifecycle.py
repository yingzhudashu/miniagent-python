"""Config watcher task creation tests; lifecycle ownership is tested separately."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from miniagent.assistant.infrastructure import config_watch


@pytest.mark.asyncio
async def test_config_watch_uses_supplied_lifecycle_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = SimpleNamespace()
    stop_event = asyncio.Event()
    started = asyncio.Event()

    async def watch_loop(actual_container: object, actual_stop: asyncio.Event) -> None:
        assert actual_container is container
        assert actual_stop is stop_event
        started.set()
        await actual_stop.wait()

    monkeypatch.setattr(config_watch, "get_config", lambda *_args: True)
    monkeypatch.setattr(config_watch, "_config_watch_loop", watch_loop)

    task = config_watch.start_config_watch(container, stop_event)
    assert task is not None
    await started.wait()
    stop_event.set()
    await task


def test_config_watch_disabled_does_not_create_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_watch, "get_config", lambda *_args: False)
    assert config_watch.start_config_watch(SimpleNamespace(), asyncio.Event()) is None
