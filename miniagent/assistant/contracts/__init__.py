"""Stable, platform-neutral contracts at the center of MiniAgent."""

from miniagent.agent.defaults import AGENT_HISTORY_SIZE_DEFAULT
from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.ports.runtime import (
    ActivityLogProtocol,
    KeywordIndexProtocol,
    OnPlan,
    OnThinking,
    OnThinkingCallback,
    OnToolCall,
    OnToolFinish,
    OnToolFinishCallback,
)
from miniagent.assistant.contracts.channels import ChannelAdapter, ChannelRegistryProtocol
from miniagent.assistant.contracts.configuration import ConfigSnapshot
from miniagent.assistant.contracts.lifecycle import HealthReport, HealthState, LifecycleService
from miniagent.assistant.contracts.messages import (
    Attachment,
    ChannelTarget,
    InboundMessage,
    OutboundEvent,
    OutboundEventKind,
)
from miniagent.assistant.contracts.messaging import (
    InboundQueueProtocol,
    InboundTurnHandler,
    OrderedOutboundDispatcherProtocol,
    QueueKeyResolver,
)
from miniagent.assistant.contracts.runtime import (
    ChannelRouterProtocol,
    FeishuRuntimeProtocol,
    MessageQueueProtocol,
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
