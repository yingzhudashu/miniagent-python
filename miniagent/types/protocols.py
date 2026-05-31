"""Mini Agent Python — Protocol 类型定义

定义核心接口的 Protocol 类型，替代 Any 类型，提升类型安全性。

使用场景：
- ``runtime/context.py`` RuntimeContext 字段类型
- ``core/executor.py`` 回调函数签名
- ``core/agent.py`` 参数类型

注意：Protocol 仅用于类型检查，不影响运行时行为。

**注意**：ToolRegistryProtocol 和 ToolMonitorProtocol 已在各自模块定义：
- ToolRegistryProtocol: miniagent/types/tool.py
- ToolMonitorProtocol: miniagent/types/agent.py
本模块仅定义运行时注入相关的 Protocol。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from miniagent.types.memory import MemoryData


@runtime_checkable
class MemoryStoreProtocol(Protocol):
    """记忆存储接口协议。

    定义核心记忆操作方法，供 RuntimeContext 和 agent 参数使用。
    """

    _state_dir: str

    async def load(self, session_key: str) -> MemoryData | None: ...
    async def update_summary(
        self, session_key: str, summary: str, facts: list[str]
    ) -> None: ...
    async def update_user_snippet(self, session_key: str, snippet: str) -> None: ...
    async def append_message(
        self, session_key: str, role: str, content: str
    ) -> None: ...


@runtime_checkable
class ActivityLogProtocol(Protocol):
    """活动日志接口协议。

    定义日志记录方法，用于追踪会话活动。
    """

    def log_session_start(
        self, session_key: str, user_input: str, source: str
    ) -> None: ...
    def log_llm_call(
        self,
        session_key: str,
        turn: int,
        model: str,
        message_count: int,
        tool_count: int,
        thinking: str,
        token_usage: dict[str, Any] | None,
    ) -> None: ...
    def log_tool_call(
        self,
        session_key: str,
        tool_name: str,
        intent: str,
        args: dict[str, Any],
        result: str,
        duration_ms: int,
        success: bool,
    ) -> None: ...
    def log_final_reply(self, session_key: str, reply: str) -> None: ...


@runtime_checkable
class KeywordIndexProtocol(Protocol):
    """关键词索引接口协议。

    定义语义检索方法。
    """

    def search_relevant(
        self, query: str, limit: int = 10, recent_minutes: int = 0
    ) -> list[Any]: ...
    def index_entry(self, session_key: str, entry: Any) -> None: ...
    def save(self) -> None: ...
    def get_stats(self) -> dict[str, Any]: ...


class OnThinkingCallback(Protocol):
    """思考回调接口协议。

    定义流式思考输出回调签名。
    """

    async def __call__(
        self,
        text: str,
        streaming: bool,
        header: str,
        *,
        full_record: str | None = None,
        reset: bool = False,
        is_last_step: bool = False,
    ) -> None: ...


class OnToolFinishCallback(Protocol):
    """工具完成回调接口协议。

    定义工具执行完成回调签名。
    """

    async def __call__(
        self,
        name: str,
        args_json: str,
        result: str,
        success: bool,
        *,
        thinking_header: str | None = None,
    ) -> None: ...


__all__ = [
    "MemoryStoreProtocol",
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "OnThinkingCallback",
    "OnToolFinishCallback",
]