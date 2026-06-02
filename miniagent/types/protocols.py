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
    from miniagent.types.memory import SessionMemory
    from miniagent.types.skill import ClawHubClientProtocol


@runtime_checkable
class MemoryStoreProtocol(Protocol):
    """记忆存储接口协议。

    定义核心记忆操作方法，供 RuntimeContext 和 agent 参数使用。
    """

    _state_dir: str

    async def load(self, session_key: str) -> SessionMemory | None: ...
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


# ============================================================================
# RuntimeContext 相关 Protocol（新增）
# ============================================================================


class UnifiedEngineProtocol(Protocol):
    """统一引擎接口协议。

    定义引擎核心方法，用于 RuntimeContext.engine 字段类型。
    """

    async def run_agent_with_thinking(
        self,
        user_input: str,
        registry: Any,
        monitor: Any,
        session_manager: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def inject_message(
        self,
        session_key: str,
        message: str,
        session_manager: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...


class ChannelRouterProtocol(Protocol):
    """通道路由器接口协议。

    定义通道与会话绑定方法，用于 RuntimeContext.channel_router 字段类型。
    """

    CLI_CHANNEL: str
    FEISHU_P2P_PREFIX: str
    FEISHU_GROUP_PREFIX: str

    def bind(self, channel_id: str, session_id: str) -> str: ...
    def unbind(self, channel_id: str) -> str: ...
    def resolve(self, channel_id: str) -> str: ...
    def get_bound_channels(self, session_id: str) -> list[str]: ...
    def set_primary(self, session_id: str) -> None: ...
    def get_primary(self) -> str | None: ...


class MessageQueueProtocol(Protocol):
    """消息队列管理器接口协议。

    定义消息队列方法，用于 RuntimeContext.message_queue 字段类型。
    """

    exec_lock: Any | None

    def enqueue(
        self,
        chat_id: str,
        coro: Any,
        mode: Any = None,
        on_start: Any = None,
        on_done: Any = None,
    ) -> None: ...

    def abort_pending(self, chat_id: str) -> int: ...
    def get_queue_status(self, chat_id: str) -> dict[str, Any]: ...
    def get_all_queue_status(self) -> dict[str, dict[str, Any]]: ...


class FeishuRuntimeProtocol(Protocol):
    """飞书运行时接口协议。

    定义飞书 WebSocket 生命周期方法，用于 RuntimeContext.feishu 字段类型。
    """

    def start(
        self,
        skill_toolboxes: list,
        skill_prompts: list,
        create_handler: Any,
        state: dict | None = None,
        **kwargs: Any,
    ) -> None: ...

    def stop(self) -> None: ...
    def is_running(self) -> bool: ...


__all__ = [
    "MemoryStoreProtocol",
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "OnThinkingCallback",
    "OnToolFinishCallback",
    "UnifiedEngineProtocol",
    "ChannelRouterProtocol",
    "MessageQueueProtocol",
    "FeishuRuntimeProtocol",
]