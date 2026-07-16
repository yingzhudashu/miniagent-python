"""Process entrypoint and dependency composition for MiniAgent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.assistant.application.messaging.channels import ChannelRegistry
from miniagent.assistant.bootstrap.application import ApplicationContainer
from miniagent.assistant.contracts.runtime import MessageQueueProtocol

_logger = logging.getLogger(__name__)


def create_application_container() -> ApplicationContainer:
    """Construct the process-scoped dependency graph from loaded configuration."""
    from miniagent.agent.monitor import DefaultToolMonitor
    from miniagent.assistant.engine.background_tasks import BackgroundTaskManager
    from miniagent.assistant.engine.feishu_state import FeishuRuntime
    from miniagent.assistant.engine.turn_service import AssistantTurnService
    from miniagent.assistant.infrastructure.channel_router import ChannelRouter
    from miniagent.assistant.infrastructure.json_config import (
        get_config,
        get_configuration_service,
    )
    from miniagent.assistant.infrastructure.message_queue import MessageQueueManager, QueueMode
    from miniagent.assistant.infrastructure.paths import resolve_state_dir
    from miniagent.assistant.infrastructure.registry import DefaultToolRegistry
    from miniagent.assistant.knowledge.registry import KnowledgeRegistry
    from miniagent.assistant.memory.runtime import create_memory_runtime
    from miniagent.assistant.skills import DefaultSkillRegistry, create_clawhub_client
    from miniagent.llm.factory import create_llm_gateway

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
    llm_gateway = create_llm_gateway(
        get_config,
        cache_path=Path(resolve_state_dir()) / "llm-model-catalog.json",
    )
    return ApplicationContainer(
        registry=DefaultToolRegistry(),
        monitor=DefaultToolMonitor(),
        skill_registry=DefaultSkillRegistry(),
        clawhub=create_clawhub_client(),
        engine=AssistantTurnService(),
        channel_router=channel_router,
        message_queue=cast(MessageQueueProtocol, message_queue),
        feishu=FeishuRuntime(message_queue),
        memory=cast(MemoryRuntimeProtocol, memory),
        knowledge_registry=KnowledgeRegistry(),
        background_tasks=BackgroundTaskManager(),
        config=get_configuration_service(),
        outbound_channels=ChannelRegistry(),
        llm_gateway=llm_gateway,
    )


__all__ = ["create_application_container"]
