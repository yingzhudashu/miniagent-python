"""Application composition and lifecycle primitives."""

from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.bootstrap.lifecycle import (
    LifecycleManager,
    LifecyclePhase,
    LifecycleShutdownError,
    LifecycleStartupError,
    ServiceFailure,
)
from miniagent.assistant.bootstrap.task_service import AsyncTaskLifecycleService

__all__ = [
    "ApplicationContainer",
    "AsyncTaskLifecycleService",
    "LifecycleManager",
    "LifecyclePhase",
    "LifecycleShutdownError",
    "LifecycleStartupError",
    "ServiceFailure",
]
