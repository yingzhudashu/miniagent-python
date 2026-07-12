"""Stable, platform-neutral contracts at the center of MiniAgent."""

from miniagent.contracts.channels import ChannelAdapter, ChannelRegistryProtocol
from miniagent.contracts.configuration import ConfigSnapshot
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
from miniagent.contracts.runtime import (
    ActivityLogProtocol,
    ChannelRouterProtocol,
    FeishuRuntimeProtocol,
    KeywordIndexProtocol,
    MessageQueueProtocol,
    OnPlan,
    OnThinking,
    OnThinkingCallback,
    OnToolCall,
    OnToolFinish,
    OnToolFinishCallback,
    UnifiedEngineProtocol,
)

__all__ = [
    "AGENT_HISTORY_SIZE_DEFAULT",
    "ActivityLogProtocol",
    "Attachment",
    "ChannelAdapter",
    "ChannelRegistryProtocol",
    "ChannelRouterProtocol",
    "ChannelTarget",
    "ConfigSnapshot",
    "FeishuRuntimeProtocol",
    "HealthReport",
    "HealthState",
    "InboundMessage",
    "InboundQueueProtocol",
    "InboundTurnHandler",
    "LifecycleService",
    "KnowledgeRegistryProtocol",
    "KeywordIndexProtocol",
    "MemoryRuntimeProtocol",
    "MessageQueueProtocol",
    "OnPlan",
    "OnThinking",
    "OnThinkingCallback",
    "OnToolCall",
    "OnToolFinish",
    "OnToolFinishCallback",
    "OutboundEvent",
    "OutboundEventKind",
    "OrderedOutboundDispatcherProtocol",
    "QueueKeyResolver",
    "UnifiedEngineProtocol",
]
