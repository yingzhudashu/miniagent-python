"""Compatibility re-exports for the public protocol-neutral LLM contracts."""

from miniagent.llm.types import (
    LLMCompletion,
    LLMFailureInfo,
    LLMFunctionCall,
    LLMStreamEvent,
    LLMToolCall,
    LLMToolCallDelta,
    LLMTransportError,
)

__all__ = [
    "LLMCompletion",
    "LLMFailureInfo",
    "LLMFunctionCall",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolCallDelta",
    "LLMTransportError",
]
