"""Stable, platform-neutral contracts at the center of MiniAgent."""

from miniagent.contracts.channels import ChannelAdapter, ChannelRegistryProtocol
from miniagent.contracts.defaults import AGENT_HISTORY_SIZE_DEFAULT
from miniagent.contracts.knowledge import KnowledgeRegistryProtocol
from miniagent.contracts.lifecycle import HealthReport, HealthState, LifecycleService
from miniagent.contracts.memory import MemoryRuntimeProtocol
from miniagent.contracts.messages import (
    Attachment,
    ChannelTarget,
    InboundMessage,
    OutboundEvent,
    OutboundEventKind,
)
from miniagent.contracts.messaging import (
    InboundQueueProtocol,
    InboundTurnHandler,
    OrderedOutboundDispatcherProtocol,
    QueueKeyResolver,
)

__all__ = [
    "AGENT_HISTORY_SIZE_DEFAULT",
    "Attachment",
    "ChannelAdapter",
    "ChannelRegistryProtocol",
    "ChannelTarget",
    "HealthReport",
    "HealthState",
    "InboundMessage",
    "InboundQueueProtocol",
    "InboundTurnHandler",
    "LifecycleService",
    "KnowledgeRegistryProtocol",
    "MemoryRuntimeProtocol",
    "OutboundEvent",
    "OutboundEventKind",
    "OrderedOutboundDispatcherProtocol",
    "QueueKeyResolver",
]
