"""Config watcher task creation tests; lifecycle ownership is tested separately."""

from __future__ import annotations

import asyncio
from pathlib import Path
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


@pytest.mark.asyncio
async def test_config_mtime_async_handles_existing_and_missing_files(tmp_path: Path) -> None:
    config_file = tmp_path / "config.user.json"
    config_file.write_text("{}", encoding="utf-8")

    assert await config_watch._config_mtime_async(config_file) == config_file.stat().st_mtime
    config_file.unlink()
    assert await config_watch._config_mtime_async(config_file) is None


@pytest.mark.asyncio
async def test_config_watch_reloads_only_after_stable_threaded_mtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = asyncio.Event()
    mtimes = iter((1.0, 2.0, 2.0))
    reloaded: list[object] = []
    container = SimpleNamespace()

    async def mtime(_path: Path) -> float | None:
        return next(mtimes)

    async def reload_runtime_config(actual: object) -> None:
        reloaded.append(actual)
        stop_event.set()

    from miniagent.assistant.infrastructure import json_config

    monkeypatch.setattr(config_watch, "_config_mtime_async", mtime)
    monkeypatch.setattr(config_watch, "_CHECK_INTERVAL", 0.001)
    monkeypatch.setattr(config_watch, "_DEBOUNCE_SEC", 0)
    monkeypatch.setattr(json_config, "reload_runtime_config", reload_runtime_config)

    await config_watch._config_watch_loop(container, stop_event)

    assert reloaded == [container]


@pytest.mark.asyncio
async def test_config_watch_skips_reload_when_file_disappears_during_debounce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop_event = asyncio.Event()
    calls = 0

    async def mtime(_path: Path) -> float | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 1.0
        if calls == 2:
            return 2.0
        if calls == 3:
            return None
        stop_event.set()
        return None

    monkeypatch.setattr(config_watch, "_config_mtime_async", mtime)
    monkeypatch.setattr(config_watch, "_CHECK_INTERVAL", 0.001)
    monkeypatch.setattr(config_watch, "_DEBOUNCE_SEC", 0)

    await config_watch._config_watch_loop(SimpleNamespace(), stop_event)

    assert calls == 4
