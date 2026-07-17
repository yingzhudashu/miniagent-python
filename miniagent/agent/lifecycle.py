"""Reusable lifecycle contracts and deterministic service coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class HealthState(str, Enum):
    """Stable health states shared by Agent extensions and UI surfaces."""

    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Immutable non-blocking health snapshot."""

    state: HealthState
    detail: str = ""
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class LifecycleService(Protocol):
    """Lifecycle implemented by Agent extensions and infrastructure services."""

    @property
    def name(self) -> str: ...

    async def initialize(self) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def health(self) -> HealthReport: ...


class LifecyclePhase(str, Enum):
    NEW = "new"
    INITIALIZED = "initialized"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ServiceFailure:
    service_name: str
    error: BaseException


class LifecycleStartupError(RuntimeError):
    """A service failed to initialize/start after rollback was attempted."""

    def __init__(
        self,
        stage: str,
        service_name: str,
        cause: BaseException,
        rollback_failures: tuple[ServiceFailure, ...] = (),
    ) -> None:
        self.stage = stage
        self.service_name = service_name
        self.cause = cause
        self.rollback_failures = rollback_failures
        suffix = f"; rollback failures={len(rollback_failures)}" if rollback_failures else ""
        super().__init__(f"service {service_name!r} failed during {stage}: {cause}{suffix}")


class LifecycleShutdownError(RuntimeError):
    """Every service was stopped, but at least one stop operation failed."""

    def __init__(self, failures: tuple[ServiceFailure, ...]) -> None:
        self.failures = failures
        names = ", ".join(failure.service_name for failure in failures)
        super().__init__(f"failed to stop {len(failures)} service(s): {names}")


class LifecycleManager:
    """Start in registration order and stop exactly once in reverse order."""

    def __init__(self, services: Iterable[LifecycleService]) -> None:
        self._services = tuple(services)
        names = [service.name for service in self._services]
        if any(not name.strip() for name in names):
            raise ValueError("lifecycle service names must not be empty")
        if len(names) != len(set(names)):
            raise ValueError("lifecycle service names must be unique")
        self._attempted: list[LifecycleService] = []
        self._initialized: set[str] = set()
        self._started: set[str] = set()
        self._phase = LifecyclePhase.NEW
        self._lock = asyncio.Lock()

    @property
    def phase(self) -> LifecyclePhase:
        return self._phase

    @property
    def service_names(self) -> tuple[str, ...]:
        return tuple(service.name for service in self._services)

    def service(self, name: str) -> LifecycleService:
        for service in self._services:
            if service.name == name:
                return service
        raise KeyError(f"unknown lifecycle service: {name}")

    async def initialize(self) -> None:
        async with self._lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        if self._phase in (LifecyclePhase.INITIALIZED, LifecyclePhase.READY):
            return
        self._ensure_startable()
        for service in self._services:
            if service.name in self._initialized:
                continue
            if service not in self._attempted:
                self._attempted.append(service)
            try:
                await service.initialize()
            except BaseException as error:
                self._phase = LifecyclePhase.FAILED
                failures = await self._stop_attempted()
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise LifecycleStartupError(
                    "initialize", service.name, error, failures
                ) from error
            self._initialized.add(service.name)
        self._phase = LifecyclePhase.INITIALIZED

    async def start(self) -> None:
        async with self._lock:
            if self._phase is LifecyclePhase.READY:
                return
            await self._initialize_locked()
            for service in self._services:
                if service.name in self._started:
                    continue
                try:
                    await service.start()
                except BaseException as error:
                    self._phase = LifecyclePhase.FAILED
                    failures = await self._stop_attempted()
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise LifecycleStartupError(
                        "start", service.name, error, failures
                    ) from error
                self._started.add(service.name)
            self._phase = LifecyclePhase.READY

    async def stop(self) -> None:
        async with self._lock:
            if self._phase is LifecyclePhase.STOPPED:
                return
            self._phase = LifecyclePhase.STOPPING
            failures = await self._stop_attempted()
            if failures:
                self._phase = LifecyclePhase.FAILED
                raise LifecycleShutdownError(failures)
            self._phase = LifecyclePhase.STOPPED

    def health(self) -> dict[str, HealthReport]:
        return {service.name: service.health() for service in self._services}

    def _ensure_startable(self) -> None:
        if self._phase in (LifecyclePhase.STOPPED, LifecyclePhase.FAILED):
            raise RuntimeError(f"lifecycle manager cannot start from {self._phase.value}")

    async def _stop_attempted(self) -> tuple[ServiceFailure, ...]:
        failures: list[ServiceFailure] = []
        attempted = tuple(reversed(self._attempted))
        self._attempted.clear()
        self._initialized.clear()
        self._started.clear()
        for service in attempted:
            try:
                await service.stop()
            except BaseException as error:
                failures.append(ServiceFailure(service.name, error))
        return tuple(failures)


__all__ = [
    "HealthReport",
    "HealthState",
    "LifecycleManager",
    "LifecyclePhase",
    "LifecycleService",
    "LifecycleShutdownError",
    "LifecycleStartupError",
    "ServiceFailure",
]
