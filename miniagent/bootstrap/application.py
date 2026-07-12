"""Typed application dependency container shared by entry points and channel adapters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from miniagent.application.messaging import ChannelRegistry

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from miniagent.bootstrap.lifecycle import LifecycleManager
    from miniagent.contracts.channels import ChannelRegistryProtocol
    from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
    from miniagent.contracts.memory import MemoryRuntimeProtocol
    from miniagent.contracts.messaging import OrderedOutboundDispatcherProtocol
    from miniagent.types.agent import ToolMonitorProtocol
    from miniagent.types.protocols import (
        ChannelRouterProtocol,
        FeishuRuntimeProtocol,
        MessageQueueProtocol,
        UnifiedEngineProtocol,
    )
    from miniagent.types.skill import ClawHubClientProtocol, SkillRegistryProtocol
    from miniagent.types.tool import ToolRegistryProtocol


@dataclass
class ApplicationContainer:
    """Single process-scoped composition root for all runtime dependencies.

    The entrypoint constructs exactly one container and passes it explicitly to
    runtime services, command handlers and channel adapters. Mutable fields below
    are owned runtime resources or callbacks registered by the active CLI surface;
    they are deliberately kept here so shutdown has one authoritative owner.
    """

    registry: ToolRegistryProtocol
    monitor: ToolMonitorProtocol
    skill_registry: SkillRegistryProtocol
    clawhub: ClawHubClientProtocol | None
    engine: UnifiedEngineProtocol
    channel_router: ChannelRouterProtocol
    message_queue: MessageQueueProtocol
    feishu: FeishuRuntimeProtocol
    memory: MemoryRuntimeProtocol
    knowledge_registry: KnowledgeRegistryProtocol
    background_tasks: Any
    outbound_channels: ChannelRegistryProtocol = field(default_factory=ChannelRegistry)
    cli_outbound_dispatcher: OrderedOutboundDispatcherProtocol | None = None
    lifecycle_manager: LifecycleManager | None = field(default=None, repr=False)
    openai_client: Any | None = None
    retired_openai_clients: list[Any] = field(default_factory=list, repr=False)
    create_feishu_handler_factory: Callable[..., Any] | None = field(default=None, repr=False)
    cli_transcript_append_ansi: Callable[[Any], None] | None = field(default=None, repr=False)
    cli_transcript_append: Callable[[str, str], None] | None = field(default=None, repr=False)
    cli_transcript_coordinator: Any | None = field(default=None, repr=False)
    shutdown_tracked_tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)

    def register_shutdown_tracked_task(self, task: asyncio.Task[Any]) -> None:
        """Track a live task until completion so shutdown can cancel and await it."""
        if task.done() or task in self.shutdown_tracked_tasks:
            return
        self.shutdown_tracked_tasks.add(task)

        def _done(completed: asyncio.Task[Any]) -> None:
            self.shutdown_tracked_tasks.discard(completed)
            if completed.cancelled():
                return
            error = completed.exception()
            if error is not None:
                _logger.error("shutdown-tracked task failed: %s", error, exc_info=error)

        task.add_done_callback(_done)


__all__ = ["ApplicationContainer"]
