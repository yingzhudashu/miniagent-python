"""Deterministic application service startup, rollback and shutdown coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from miniagent.contracts.lifecycle import HealthReport, LifecycleService


class LifecyclePhase(str, Enum):
    """Aggregate lifecycle phase for the application service graph."""

    NEW = "new"
    INITIALIZED = "initialized"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ServiceFailure:
    """Failure captured while rolling back or stopping a service graph."""

    service_name: str
    error: BaseException


class LifecycleStartupError(RuntimeError):
    """Initialization or startup failed after all attempted services were rolled back."""

    def __init__(
        self,
        stage: str,
        service_name: str,
        cause: BaseException,
        rollback_failures: tuple[ServiceFailure, ...] = (),
    ) -> None:
        """Record the primary failure and any best-effort rollback failures."""
        self.stage = stage
        self.service_name = service_name
        self.cause = cause
        self.rollback_failures = rollback_failures
        suffix = f"; rollback failures={len(rollback_failures)}" if rollback_failures else ""
        super().__init__(f"service {service_name!r} failed during {stage}: {cause}{suffix}")


class LifecycleShutdownError(RuntimeError):
    """One or more services failed to stop after every service was attempted."""

    def __init__(self, failures: tuple[ServiceFailure, ...]) -> None:
        """Expose all failures without relying on Python 3.11 ExceptionGroup."""
        self.failures = failures
        names = ", ".join(failure.service_name for failure in failures)
        super().__init__(f"failed to stop {len(failures)} service(s): {names}")


class LifecycleManager:
    """Start services in registration order and stop them exactly once in reverse order."""

    def __init__(self, services: Iterable[LifecycleService]) -> None:
        """Create a manager and reject ambiguous duplicate service names."""
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
        """Return the current aggregate phase without blocking."""
        return self._phase

    @property
    def service_names(self) -> tuple[str, ...]:
        """Return services in their deterministic startup order."""
        return tuple(service.name for service in self._services)

    def service(self, name: str) -> LifecycleService:
        """Return a registered service by its stable name."""
        for service in self._services:
            if service.name == name:
                return service
        raise KeyError(f"unknown lifecycle service: {name}")

    async def initialize(self) -> None:
        """Initialize every service or roll back all attempted services."""
        async with self._lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> None:
        """Initialize while the caller holds ``_lock``."""
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
        """Initialize if needed, then start every service with rollback on failure."""
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
                    raise LifecycleStartupError("start", service.name, error, failures) from error
                self._started.add(service.name)
            self._phase = LifecyclePhase.READY

    async def stop(self) -> None:
        """Stop all attempted services in reverse order; repeated calls are no-ops."""
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
        """Return one health snapshot per service in registration order."""
        return {service.name: service.health() for service in self._services}

    def _ensure_startable(self) -> None:
        """Reject attempts to reuse a terminal lifecycle manager."""
        if self._phase in (LifecyclePhase.STOPPED, LifecyclePhase.FAILED):
            raise RuntimeError(f"lifecycle manager cannot start from {self._phase.value}")

    async def _stop_attempted(self) -> tuple[ServiceFailure, ...]:
        """Best-effort stop attempted services and clear active bookkeeping."""
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
    "LifecycleManager",
    "LifecyclePhase",
    "LifecycleShutdownError",
    "LifecycleStartupError",
    "ServiceFailure",
]
