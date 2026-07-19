"""Public product application and composition entry points."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer
    from miniagent.assistant.composition import ComposedAssistantRuntime
    from miniagent.assistant.spec import AssistantSpec, PersonalAssistantSpec


@dataclass(slots=True)
class AssistantApplication:
    """Own the process-scoped dependency graph and its runtime lifecycle."""

    container: ApplicationContainer | ComposedAssistantRuntime

    def run(self) -> None:
        """运行容器拥有的单一异步应用生命周期。"""
        if hasattr(self.container, "serve"):
            asyncio.run(self.container.serve())
            return
        from miniagent.assistant.engine.main import run_runtime

        asyncio.run(run_runtime(self.container))

    async def start(self) -> None:
        """Start a V4 composed application without owning the event loop."""
        start = getattr(self.container, "start", None)
        if not callable(start):
            raise RuntimeError("bundled container application must be started with run()")
        await start()

    async def stop(self) -> None:
        """Stop a V4 composed application in reverse dependency order."""
        stop = getattr(self.container, "stop", None)
        if callable(stop):
            await stop()

    def health(self) -> Any:
        """Return the composed runtime health snapshot when available."""
        health = getattr(self.container, "health", None)
        return health() if callable(health) else None


def create_assistant(spec: AssistantSpec) -> AssistantApplication:
    """Build one isolated Assistant from a declarative specification."""
    if spec.container_factory is not None:
        return AssistantApplication(spec.container_factory())
    from miniagent.assistant.composition import ComposedAssistantRuntime

    return AssistantApplication(ComposedAssistantRuntime(spec))


def _personal_container() -> ApplicationContainer:
    from miniagent.assistant.bootstrap.entrypoint import create_application_container

    return create_application_container()


def personal_assistant_spec() -> PersonalAssistantSpec:
    """Return the bundled personal-assistant recipe without constructing it."""
    from miniagent.assistant.spec import PersonalAssistantSpec

    return PersonalAssistantSpec(
        name="personal",
        container_factory=_personal_container,
        metadata={"recipe": "personal", "version": 4},
    )


def create_personal_assistant() -> AssistantApplication:
    """Compose the bundled CLI/TUI/Feishu personal assistant."""
    return create_assistant(personal_assistant_spec())


def create_assistant_application() -> AssistantApplication:
    """Compatibility name for :func:`create_personal_assistant`."""
    return create_personal_assistant()


def run_assistant(argv: list[str] | None = None) -> None:
    """Handle the command-line boundary and run the single application lifecycle."""
    from miniagent.assistant.runner import run_cli_boundary

    run_cli_boundary(argv)


__all__ = [
    "AssistantApplication",
    "create_assistant",
    "create_assistant_application",
    "create_personal_assistant",
    "personal_assistant_spec",
    "run_assistant",
]
