"""Application composition and lifecycle primitives."""

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.bootstrap.lifecycle import (
    LifecycleManager,
    LifecyclePhase,
    LifecycleShutdownError,
    LifecycleStartupError,
    ServiceFailure,
)
from miniagent.bootstrap.task_service import AsyncTaskLifecycleService

__all__ = [
    "ApplicationContainer",
    "AsyncTaskLifecycleService",
    "LifecycleManager",
    "LifecyclePhase",
    "LifecycleShutdownError",
    "LifecycleStartupError",
    "ServiceFailure",
]
