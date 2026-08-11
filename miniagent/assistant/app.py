"""Product factories for the two explicit Assistant composition paths."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer
    from miniagent.assistant.composition import ComposedAssistantRuntime
    from miniagent.assistant.spec import AssistantSpec


@dataclass(slots=True)
class PersonalAssistantApplication:
    """Own the bundled product container and its event-loop boundary."""

    container: ApplicationContainer

    def run(self) -> None:
        """运行容器拥有的单一异步应用生命周期。"""
        from miniagent.assistant.engine.main import run_runtime

        asyncio.run(run_runtime(self.container))


def create_assistant(spec: AssistantSpec) -> ComposedAssistantRuntime:
    """Build one isolated Assistant from a declarative specification."""
    from miniagent.assistant.composition import ComposedAssistantRuntime

    return ComposedAssistantRuntime(spec)


def create_personal_assistant() -> PersonalAssistantApplication:
    """Compose the bundled CLI/TUI/Feishu personal assistant."""
    from miniagent.assistant.bootstrap.entrypoint import create_application_container

    return PersonalAssistantApplication(create_application_container())


__all__ = [
    "PersonalAssistantApplication",
    "create_assistant",
    "create_personal_assistant",
]
