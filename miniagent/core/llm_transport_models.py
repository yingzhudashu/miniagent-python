"""Compatibility re-exports for the public protocol-neutral LLM contracts."""

from miniagent.contracts.llm import (
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
