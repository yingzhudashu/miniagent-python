"""Chat Completions 与 Responses 共用的协议无关传输模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LLMFunctionCall:
    """与底层 OpenAI SDK 无关的函数调用载荷。"""

    name: str
    arguments: str


@dataclass(slots=True)
class LLMToolCall:
    """统一表示 Chat Completions 与 Responses 的工具调用。"""

    id: str
    function: LLMFunctionCall
    _args_dict: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    """传输层返回给上层的协议无关完成结果。"""

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
    """流式工具调用的增量片段。"""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(slots=True)
class LLMStreamEvent:
    """协议无关的流式文本、工具或终态事件。"""

    content_delta: str | None = None
    tool_call_delta: LLMToolCallDelta | None = None
    usage: Any | None = None
    completed: bool = False
    status: str | None = None
    output_item_types: tuple[str, ...] = ()
    incomplete_reason: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LLMFailureInfo:
    """经过脱敏和归类的网关失败信息。"""

    category: str
    retryable: bool
    status_code: int | None = None


class LLMTransportError(RuntimeError):
    """为已识别网关故障提供不泄露响应正文的异常。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "LLMCompletion",
    "LLMFailureInfo",
    "LLMFunctionCall",
    "LLMStreamEvent",
    "LLMToolCall",
    "LLMToolCallDelta",
    "LLMTransportError",
]
