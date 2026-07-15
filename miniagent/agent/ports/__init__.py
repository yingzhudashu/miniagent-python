"""Injected ports used by the provider- and presentation-neutral agent core."""

from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
from miniagent.agent.ports.memory import MemoryRuntimeProtocol
from miniagent.agent.ports.runtime import (
    OnPlan,
    OnThinking,
    OnThinkingCallback,
    OnToolCall,
    OnToolFinish,
    OnToolFinishCallback,
)

__all__ = [
    "KnowledgeRegistryProtocol",
    "MemoryRuntimeProtocol",
    "OnPlan",
    "OnThinking",
    "OnThinkingCallback",
    "OnToolCall",
    "OnToolFinish",
    "OnToolFinishCallback",
]
