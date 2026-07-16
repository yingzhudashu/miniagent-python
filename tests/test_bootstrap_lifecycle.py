"""Failure-oriented tests for deterministic service lifecycle coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from miniagent.assistant.bootstrap.lifecycle import (
    LifecycleManager,
    LifecyclePhase,
    LifecycleShutdownError,
    LifecycleStartupError,
)
from miniagent.assistant.contracts.lifecycle import HealthReport, HealthState


@dataclass
class FakeService:
    """Small controllable lifecycle service that records all calls."""

    name: str
    events: list[str]
    fail_initialize: bool = False
    fail_start: bool = False
    fail_stop: bool = False
    cancel_initialize: bool = False
    stop_calls: int = 0
    state: HealthState = field(default=HealthState.STOPPED)

    async def initialize(self) -> None:
        """Record initialization and optionally fail or cancel."""
        self.events.append(f"init:{self.name}")
        self.state = HealthState.STARTING
        if self.cancel_initialize:
            raise asyncio.CancelledError
        if self.fail_initialize:
            raise ValueError(f"init failed: {self.name}")

    async def start(self) -> None:
        """Record startup and optionally fail."""
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise ValueError(f"start failed: {self.name}")
        self.state = HealthState.READY

    async def stop(self) -> None:
        """Record shutdown and optionally fail."""
        self.events.append(f"stop:{self.name}")
        self.stop_calls += 1
        self.state = HealthState.STOPPED
        if self.fail_stop:
            raise ValueError(f"stop failed: {self.name}")

    def health(self) -> HealthReport:
        """Return the fake's current state."""
        return HealthReport(self.state)


@pytest.mark.asyncio
async def test_start_and_stop_use_opposite_deterministic_orders() -> None:
    events: list[str] = []
    first = FakeService("first", events)
    second = FakeService("second", events)
    manager = LifecycleManager([first, second])

    await manager.start()
    assert manager.phase is LifecyclePhase.READY
    assert events == ["init:first", "init:second", "start:first", "start:second"]
    assert manager.health()["second"].state is HealthState.READY

    await manager.stop()
    await manager.stop()
    assert manager.phase is LifecyclePhase.STOPPED
    assert events[-2:] == ["stop:second", "stop:first"]
    assert first.stop_calls == second.stop_calls == 1


@pytest.mark.asyncio
async def test_initialize_failure_rolls_back_failing_and_prior_services() -> None:
    events: list[str] = []
    first = FakeService("first", events)
    second = FakeService("second", events, fail_initialize=True)
    manager = LifecycleManager([first, second])

    with pytest.raises(LifecycleStartupError) as caught:
        await manager.initialize()

    assert caught.value.stage == "initialize"
    assert caught.value.service_name == "second"
    assert manager.phase is LifecyclePhase.FAILED
    assert events == ["init:first", "init:second", "stop:second", "stop:first"]


@pytest.mark.asyncio
async def test_start_failure_rolls_back_every_initialized_service() -> None:
    events: list[str] = []
    first = FakeService("first", events)
    second = FakeService("second", events, fail_start=True)
    manager = LifecycleManager([first, second])

    with pytest.raises(LifecycleStartupError) as caught:
        await manager.start()

    assert caught.value.stage == "start"
    assert events[-2:] == ["stop:second", "stop:first"]
    assert first.stop_calls == second.stop_calls == 1


@pytest.mark.asyncio
async def test_shutdown_attempts_every_service_before_raising() -> None:
    events: list[str] = []
    first = FakeService("first", events, fail_stop=True)
    second = FakeService("second", events, fail_stop=True)
    manager = LifecycleManager([first, second])
    await manager.start()

    with pytest.raises(LifecycleShutdownError) as caught:
        await manager.stop()

    assert [failure.service_name for failure in caught.value.failures] == ["second", "first"]
    assert events[-2:] == ["stop:second", "stop:first"]
    assert manager.phase is LifecyclePhase.FAILED


@pytest.mark.asyncio
async def test_cancellation_still_rolls_back_and_is_not_wrapped() -> None:
    events: list[str] = []
    service = FakeService("cancelled", events, cancel_initialize=True)
    manager = LifecycleManager([service])

    with pytest.raises(asyncio.CancelledError):
        await manager.initialize()

    assert events == ["init:cancelled", "stop:cancelled"]
    assert manager.phase is LifecyclePhase.FAILED


def test_duplicate_or_empty_service_names_are_rejected() -> None:
    events: list[str] = []
    with pytest.raises(ValueError, match="unique"):
        LifecycleManager([FakeService("same", events), FakeService("same", events)])
    with pytest.raises(ValueError, match="empty"):
        LifecycleManager([FakeService(" ", events)])
