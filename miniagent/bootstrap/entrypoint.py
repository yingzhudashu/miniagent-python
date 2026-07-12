"""Process entrypoint and dependency composition for MiniAgent."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from miniagent.application.messaging import ChannelRegistry
from miniagent.bootstrap.application import ApplicationContainer
from miniagent.contracts.memory import MemoryRuntimeProtocol
from miniagent.contracts.runtime import MessageQueueProtocol

_logger = logging.getLogger(__name__)


def create_application_container() -> ApplicationContainer:
    """Construct the process-scoped dependency graph from loaded configuration."""
    from miniagent.core.openai_client import create_async_openai_client
    from miniagent.engine.background_tasks import BackgroundTaskManager
    from miniagent.engine.engine import UnifiedEngine
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.infrastructure.channel_router import ChannelRouter
    from miniagent.infrastructure.json_config import get_config, get_config_snapshot
    from miniagent.infrastructure.message_queue import MessageQueueManager, QueueMode
    from miniagent.infrastructure.monitor import DefaultToolMonitor
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.knowledge.registry import KnowledgeRegistry
    from miniagent.memory.runtime import create_memory_runtime
    from miniagent.skills import DefaultSkillRegistry, create_clawhub_client

    memory = create_memory_runtime()

    message_queue = MessageQueueManager()
    queue_mode = str(get_config("agent.queue_mode", "queue")).strip().lower()
    try:
        message_queue.mode = QueueMode(queue_mode)
    except ValueError:
        _logger.warning(
            "未知 agent.queue_mode=%r，使用 queue；可用值: queue, preemptive",
            queue_mode,
        )
        message_queue.mode = QueueMode.QUEUE

    channel_router = ChannelRouter()
    channel_router.load()
    return ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=UnifiedEngine(),
        channel_router=channel_router,
        message_queue=cast(MessageQueueProtocol, message_queue),
        feishu=FeishuRuntime(message_queue),
        memory=cast(MemoryRuntimeProtocol, memory),
        knowledge_registry=KnowledgeRegistry(),
        background_tasks=BackgroundTaskManager(),
        config=get_config_snapshot(),
        outbound_channels=ChannelRegistry(),
        openai_client=create_async_openai_client(),
    )


def run_application() -> None:
    """Load user configuration, compose dependencies, and run the async runtime."""
    from miniagent.engine.main import run_runtime
    from miniagent.engine.setup_wizard import run_interactive_setup
    from miniagent.infrastructure.env_loader import load_secrets_from_project_root

    run_interactive_setup()
    load_secrets_from_project_root()
    asyncio.run(run_runtime(create_application_container()))


__all__ = ["create_application_container", "run_application"]
