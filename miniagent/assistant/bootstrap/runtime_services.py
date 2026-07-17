"""Assemble the production runtime service graph at the composition boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from miniagent.agent.lifecycle import LifecycleManager
from miniagent.assistant.bootstrap.task_service import AsyncTaskLifecycleService
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.feishu_lifecycle import FeishuRuntimeLifecycleService
from miniagent.assistant.infrastructure.config_watch import start_config_watch
from miniagent.assistant.scheduled_tasks.ticker import start_scheduled_tasks_ticker
from miniagent.assistant.skills.watch import start_skills_watch

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer


def build_runtime_lifecycle_manager(
    ctx: ApplicationContainer,
    state: CliLoopState,
    skill_toolboxes: list[Any],
    skill_prompts: list[Any],
    *,
    feishu_user_status: Callable[[str], None] | None = None,
) -> LifecycleManager:
    """Build services in their deterministic production startup order."""
    state_dict = cast(dict[str, Any], state)
    config_watch_stop = asyncio.Event()
    config_watch_service = AsyncTaskLifecycleService(
        "config_watch",
        starter=lambda: start_config_watch(ctx, config_watch_stop),
        signal_stop=config_watch_stop.set,
    )
    feishu_service = FeishuRuntimeLifecycleService(
        enabled=state["feishu_enabled"],
        runtime=ctx.feishu,
        handler_factory=ctx.create_feishu_handler_factory,
        state=state_dict,
        user_status=feishu_user_status,
    )

    scheduled_tasks_stop = asyncio.Event()
    scheduled_service = AsyncTaskLifecycleService(
        "scheduled_tasks",
        starter=lambda: start_scheduled_tasks_ticker(
            ctx, state, skill_toolboxes, skill_prompts, scheduled_tasks_stop
        ),
        signal_stop=scheduled_tasks_stop.set,
    )

    skills_watch_stop = asyncio.Event()
    skills_watch_service = AsyncTaskLifecycleService(
        "skills_watch",
        starter=lambda: start_skills_watch(
            ctx.registry, ctx.skill_registry, state_dict, skills_watch_stop
        ),
        signal_stop=skills_watch_stop.set,
    )
    return LifecycleManager(
        [config_watch_service, feishu_service, scheduled_service, skills_watch_service]
    )


__all__ = ["build_runtime_lifecycle_manager"]
