"""Protocol-neutral contracts for language-model providers and routing."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

LLMRole = Literal["default", "reasoning", "fast", "vision"]
WireAPI = Literal[
    "openai_chat",
    "openai_responses",
    "anthropic_messages",
    "google_generate_content",
]
StopReason = Literal["stop", "length", "tool_use", "error", "cancelled"]
ErrorCategory = Literal[
    "authentication",
    "rate_limit",
    "timeout",
    "context_length",
    "model_not_found",
    "unsupported_parameter",
    "provider_unavailable",
    "cancelled",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Capabilities used to validate role bindings before a paid request."""

    tools: bool = True
    vision: bool = False
    reasoning: bool = False
    structured_output: bool = True


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Optional USD rates per million tokens; ``None`` means unknown."""

    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    """A selectable model profile independent of an SDK model object."""

    profile: str
    provider: str
    model: str
    api: WireAPI
    display_name: str | None = None
    context_window: int = 128_000
    max_output_tokens: int = 4_096
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    pricing: ModelPricing = field(default_factory=ModelPricing)
    defaults: Mapping[str, Any] = field(default_factory=dict)
    compatibility: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Validated provider configuration resolved by the composition root."""

    provider_id: str
    driver: str
    base_url: str | None = None
    credential: str | None = None
    api_key_env: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Normalized usage shared by providers and presentation surfaces."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None

    def model_dump(self) -> dict[str, Any]:
        """Match the small serialization surface used by existing tracing code."""
        return {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True, slots=True)
class LLMFunctionCall:
    name: str
    arguments: str


@dataclass(slots=True)
class LLMToolCall:
    id: str
    function: LLMFunctionCall
    _args_dict: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    content: str | None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: Any | None = None
    model: str | None = None
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    finish_reason: str | None = None


@dataclass(slots=True)
class LLMToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class LLMStreamEvent:
    """Normalized incremental event; providers may leave unused fields empty."""

    event_type: str = "delta"
    content_delta: str | None = None
    thinking_delta: str | None = None
    tool_call_delta: LLMToolCallDelta | None = None
    usage: Any | None = None
    completed: bool = False
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LLMFailureInfo:
    category: str
    retryable: bool
    status_code: int | None = None


class LLMTransportError(RuntimeError):
    """Provider-neutral failure with safe classification metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        category: ErrorCategory = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.category = category
        self.retryable = retryable


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal provider implementation contract."""

    @property
    def provider_id(self) -> str: ...

    async def list_models(self) -> Sequence[ModelDescriptor]: ...

    async def create_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> LLMCompletion: ...

    def stream_completion(
        self,
        model: ModelDescriptor,
        *,
        messages: list[dict[str, Any]],
        params: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[LLMStreamEvent]: ...

    async def close(self) -> None: ...


__all__ = [
    "ErrorCategory",
    "LLMCompletion",
    "LLMFailureInfo",
    "LLMFunctionCall",
    "LLMProvider",
    "LLMRole",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolCallDelta",
    "LLMTransportError",
    "LLMUsage",
    "ModelCapabilities",
    "ModelDescriptor",
    "ModelPricing",
    "ProviderConfig",
    "StopReason",
    "WireAPI",
]
