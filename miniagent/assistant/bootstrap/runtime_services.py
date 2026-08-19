"""Assemble the production runtime service graph at the composition boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from miniagent.agent.constants import INSTANCE_HEARTBEAT_TIMEOUT
from miniagent.agent.lifecycle import LifecycleManager
from miniagent.assistant.bootstrap.task_service import AsyncTaskLifecycleService
from miniagent.assistant.engine.cli_state import CliLoopState
from miniagent.assistant.engine.feishu_lifecycle import FeishuRuntimeLifecycleService
from miniagent.assistant.engine.session_lock import renew_session_leases
from miniagent.assistant.infrastructure.config_watch import start_config_watch
from miniagent.assistant.infrastructure.feishu_inbound_lock import (
    renew_feishu_inbound_owner,
)
from miniagent.assistant.infrastructure.instance import heartbeat
from miniagent.assistant.scheduled_tasks.ticker import start_scheduled_tasks_ticker
from miniagent.assistant.skills.watch import start_skills_watch
from miniagent.assistant.xianyu.lifecycle import XianyuRuntimeLifecycleService

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer


async def _runtime_heartbeat_loop(stop_event: asyncio.Event) -> None:
    interval = max(1.0, INSTANCE_HEARTBEAT_TIMEOUT / 3)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            await asyncio.to_thread(heartbeat)
            await asyncio.to_thread(renew_feishu_inbound_owner)
            await asyncio.to_thread(renew_session_leases)


def _start_runtime_heartbeat(stop_event: asyncio.Event) -> asyncio.Task[None]:
    return asyncio.create_task(
        _runtime_heartbeat_loop(stop_event),
        name="miniagent_runtime_heartbeat",
    )


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
    heartbeat_stop = asyncio.Event()
    heartbeat_service = AsyncTaskLifecycleService(
        "instance_heartbeat",
        starter=lambda: _start_runtime_heartbeat(heartbeat_stop),
        signal_stop=heartbeat_stop.set,
    )
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
    if ctx.xianyu is None:
        from miniagent.assistant.xianyu.runtime import XianyuRuntime, install_xianyu_runtime

        ctx.xianyu = XianyuRuntime()
        install_xianyu_runtime(ctx.xianyu)
    xianyu_service = XianyuRuntimeLifecycleService(
        enabled=bool(state.get("xianyu_enabled", False)),
        runtime=ctx.xianyu,
        container=ctx,
        state=state_dict,
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
        [
            heartbeat_service,
            config_watch_service,
            feishu_service,
            xianyu_service,
            scheduled_service,
            skills_watch_service,
        ]
    )


__all__ = ["build_runtime_lifecycle_manager"]
