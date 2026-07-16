"""Public product application and composition entry points."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer


@dataclass(slots=True)
class AssistantApplication:
    """Own the process-scoped dependency graph and its runtime lifecycle."""

    container: ApplicationContainer

    def run(self) -> None:
        from miniagent.assistant.engine.main import run_runtime

        asyncio.run(run_runtime(self.container))


def create_assistant_application() -> AssistantApplication:
    """Compose one isolated personal-assistant application."""
    from miniagent.assistant.bootstrap.entrypoint import create_application_container

    return AssistantApplication(create_application_container())


def run_assistant(argv: list[str] | None = None) -> None:
    """Handle the command-line boundary and run the single application lifecycle."""
    from miniagent.assistant.runner import run_cli_boundary

    run_cli_boundary(argv)


__all__ = ["AssistantApplication", "create_assistant_application", "run_assistant"]
