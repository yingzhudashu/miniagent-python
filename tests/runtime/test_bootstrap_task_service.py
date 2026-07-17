"""Async background task lifecycle adapter tests."""

from __future__ import annotations

import asyncio

import pytest

from miniagent.agent.lifecycle import HealthState, LifecycleManager, LifecycleService
from miniagent.assistant.bootstrap.task_service import AsyncTaskLifecycleService


@pytest.mark.asyncio
async def test_task_service_starts_signals_and_stops_idempotently() -> None:
    """The adapter preserves cooperative signaling and awaits cancellation."""
    stop_event = asyncio.Event()
    started = asyncio.Event()

    async def run() -> None:
        started.set()
        await stop_event.wait()

    service = AsyncTaskLifecycleService(
        "ticker",
        starter=lambda: asyncio.create_task(run()),
        signal_stop=stop_event.set,
    )
    manager = LifecycleManager([service])

    await manager.start()
    await started.wait()
    assert service.health().state is HealthState.READY
    assert isinstance(service, LifecycleService)

    await manager.stop()
    await manager.stop()
    assert stop_event.is_set()
    assert service.health().state is HealthState.STOPPED


@pytest.mark.asyncio
async def test_task_service_reports_background_failure() -> None:
    """A completed task exception is visible through the health snapshot."""

    async def fail() -> None:
        raise RuntimeError("ticker failed")

    service = AsyncTaskLifecycleService(
        "ticker",
        starter=lambda: asyncio.create_task(fail()),
        signal_stop=lambda: None,
    )
    await service.initialize()
    await service.start()
    await asyncio.sleep(0)

    report = service.health()
    assert report.state is HealthState.FAILED
    assert "ticker failed" in report.detail
    await service.stop()


@pytest.mark.asyncio
async def test_task_service_start_failure_rolls_back_through_manager() -> None:
    """A synchronous starter failure participates in manager rollback."""
    stop_calls: list[str] = []

    def fail_start() -> asyncio.Task:
        raise RuntimeError("cannot start")

    service = AsyncTaskLifecycleService(
        "ticker",
        starter=fail_start,
        signal_stop=lambda: stop_calls.append("stop"),
    )
    manager = LifecycleManager([service])

    with pytest.raises(RuntimeError, match="cannot start"):
        await manager.start()
    assert stop_calls == ["stop"]


@pytest.mark.asyncio
async def test_disabled_optional_task_is_still_lifecycle_safe() -> None:
    """A disabled watcher returning None starts and stops without special casing."""
    stop_calls: list[str] = []
    service = AsyncTaskLifecycleService(
        "optional",
        starter=lambda: None,
        signal_stop=lambda: stop_calls.append("stop"),
    )
    manager = LifecycleManager([service])

    await manager.start()
    assert service.health().state is HealthState.READY
    await manager.stop()
    assert stop_calls == ["stop"]


@pytest.mark.asyncio
async def test_multiple_task_services_start_forward_and_stop_reverse() -> None:
    """Ticker and watcher follow deterministic manager ordering."""
    events: list[str] = []
    first_stop = asyncio.Event()
    second_stop = asyncio.Event()

    async def wait_for(event: asyncio.Event) -> None:
        await event.wait()

    first = AsyncTaskLifecycleService(
        "ticker",
        starter=lambda: (
            events.append("start:ticker") or asyncio.create_task(wait_for(first_stop))
        ),
        signal_stop=lambda: (events.append("stop:ticker"), first_stop.set()),
    )
    second = AsyncTaskLifecycleService(
        "watcher",
        starter=lambda: (
            events.append("start:watcher") or asyncio.create_task(wait_for(second_stop))
        ),
        signal_stop=lambda: (events.append("stop:watcher"), second_stop.set()),
    )
    manager = LifecycleManager([first, second])

    await manager.start()
    await manager.stop()
    assert events == [
        "start:ticker",
        "start:watcher",
        "stop:watcher",
        "stop:ticker",
    ]
