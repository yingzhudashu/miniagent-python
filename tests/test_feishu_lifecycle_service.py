"""Feishu runtime integration with the process lifecycle graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.bootstrap.lifecycle import LifecycleManager, LifecycleStartupError
from miniagent.assistant.contracts.lifecycle import HealthReport, HealthState, LifecycleService
from miniagent.assistant.engine.feishu_lifecycle import FeishuRuntimeLifecycleService


def _service(
    runtime: Any,
    *,
    enabled: bool = True,
    state: dict[str, Any] | None = None,
    handler_factory: Any = None,
    user_status: Any = None,
) -> FeishuRuntimeLifecycleService:
    if handler_factory is None:
        handler_factory = MagicMock(name="handler_factory")
    return FeishuRuntimeLifecycleService(
        enabled=enabled,
        runtime=runtime,
        handler_factory=handler_factory,
        state=state,
        user_status=user_status,
    )


@pytest.mark.asyncio
async def test_disabled_service_does_not_touch_runtime() -> None:
    runtime = MagicMock()
    service = _service(runtime, enabled=False)

    await service.initialize()
    await service.start()
    await service.stop()

    runtime.start.assert_not_called()
    runtime.stop.assert_not_called()
    assert service.health().state is HealthState.STOPPED
    assert isinstance(service, LifecycleService)


@pytest.mark.asyncio
async def test_start_forwards_existing_runtime_arguments() -> None:
    runtime = MagicMock()
    runtime.is_running.return_value = True
    factory = MagicMock(name="factory")
    status = MagicMock(name="status")
    state = {"instance_id": 7}
    service = _service(
        runtime,
        state=state,
        handler_factory=factory,
        user_status=status,
    )

    await service.initialize()
    await service.start()

    runtime.start.assert_called_once_with(factory, state, user_status=status)
    assert service.health().state is HealthState.READY


@pytest.mark.asyncio
async def test_stop_prefers_and_awaits_stop_async_idempotently() -> None:
    runtime = MagicMock()
    runtime.is_running.return_value = True
    runtime.stop_async = AsyncMock(return_value=None)
    service = _service(runtime)
    await service.initialize()
    await service.start()

    await service.stop()
    await service.stop()

    runtime.stop_async.assert_awaited_once()
    runtime.stop.assert_not_called()
    assert service.health().state is HealthState.STOPPED


@pytest.mark.asyncio
async def test_non_running_optional_runtime_is_stopped_not_failed() -> None:
    runtime = MagicMock()
    runtime.is_running.return_value = False
    service = _service(runtime)

    await service.initialize()
    await service.start()

    assert service.health().state is HealthState.STOPPED


@pytest.mark.asyncio
async def test_missing_enabled_dependencies_fail_initialization() -> None:
    service = _service(None)

    with pytest.raises(RuntimeError, match="runtime is unavailable"):
        await service.initialize()
    assert service.health().state is HealthState.FAILED


@pytest.mark.asyncio
async def test_activate_and_deactivate_control_runtime_ownership() -> None:
    runtime = MagicMock()
    runtime.is_running.return_value = True
    runtime.stop_async = AsyncMock(return_value=None)
    service = _service(runtime, enabled=False)

    await service.activate()
    await service.deactivate()

    runtime.start.assert_called_once()
    runtime.stop_async.assert_awaited_once()
    assert service.enabled is False


@dataclass
class _RecordingService:
    name: str
    events: list[str]
    fail_start: bool = False
    state: HealthState = field(default=HealthState.STOPPED)

    async def initialize(self) -> None:
        self.events.append(f"init:{self.name}")

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"failed: {self.name}")
        self.state = HealthState.READY

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")
        self.state = HealthState.STOPPED

    def health(self) -> HealthReport:
        return HealthReport(self.state)


@pytest.mark.asyncio
async def test_later_start_failure_rolls_back_feishu_in_reverse_order() -> None:
    events: list[str] = []

    class Runtime:
        running = False

        def start(self, *_args: Any, **_kwargs: Any) -> None:
            events.append("start:feishu")
            self.running = True

        async def stop_async(self) -> None:
            events.append("stop:feishu")
            self.running = False

        def is_running(self) -> bool:
            return self.running

    service = _service(Runtime())
    later = _RecordingService("later", events, fail_start=True)
    manager = LifecycleManager([service, later])

    with pytest.raises(LifecycleStartupError, match="later"):
        await manager.start()

    assert events == [
        "init:later",
        "start:feishu",
        "start:later",
        "stop:later",
        "stop:feishu",
    ]
