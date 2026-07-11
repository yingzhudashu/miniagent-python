"""Mini Agent Python — Protocol 类型定义

定义核心接口的 Protocol 类型，替代 Any 类型，提升类型安全性。

使用场景：
- ``runtime/context.py`` RuntimeContext 字段类型
- ``core/executor.py`` 回调函数签名
- ``core/agent.py`` 参数类型

注意：Protocol 仅用于类型检查，不影响运行时行为。

**Protocol 定义位置**：
- MemoryStoreProtocol / SessionManagerProtocol: ``miniagent/types/memory.py``
- ToolRegistryProtocol: ``miniagent/types/tool.py``
- ToolMonitorProtocol: ``miniagent/types/agent.py``
- SkillRegistryProtocol / ClawHubClientProtocol: ``miniagent/types/skill.py``
- MemoryContextProtocol 等: ``miniagent/types/memory_context.py``
- 本模块定义运行时注入特有的 Protocol（ActivityLog、KeywordIndex、回调、引擎、队列等）
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from miniagent.types.agent import ToolMonitorProtocol
from miniagent.types.confirmation import ConfirmationResult

# 从其他模块再导出 Protocol，便于统一导入
from miniagent.types.memory import MemoryStoreProtocol, SessionManagerProtocol
from miniagent.types.planning import StructuredPlan
from miniagent.types.tool import ToolRegistryProtocol


@runtime_checkable
class ActivityLogProtocol(Protocol):
    """活动日志接口协议。

    定义日志记录与维护方法，用于追踪会话活动（Layer 2 流水账）。

    实现类：``miniagent.memory.activity_log.ActivityLogger``

    Methods:
        log_session_start: 记录会话开始
        log_llm_call: 记录 LLM 调用
        log_tool_call: 记录工具调用
        log_final_reply: 记录最终回复
        get_stats: 统计日志条目与会话数
        clear_old_entries: 删除过期按日 Markdown 文件

    Note:
        - 所有日志方法为同步调用
        - ``get_stats()`` 扫描 ``base_dir`` 下 ``YYYY-MM-DD.md`` 文件
        - ``clear_old_entries()`` 按文件名日期（或 mtime）删除整文件

    See Also:
        - docs/MEMORY_SYSTEM.md: 三层记忆架构
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
        thinking: str | None,
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
    def get_stats(self) -> dict[str, Any]: ...
    def clear_old_entries(self, days: int = 30) -> int: ...


@runtime_checkable
class KeywordIndexProtocol(Protocol):
    """关键词索引接口协议。

    定义记忆关键词检索与持久化方法。

    实现类：``miniagent.memory.keyword_index.KeywordIndex``

    Methods:
        search_relevant: 按查询关键词检索相关记忆（返回 ``_SearchResult`` 或兼容结构）
        index_entry: 为一条记忆建立索引
        save: 持久化索引到磁盘
        get_stats: 返回 ``total_keywords``、``total_references``、``top_keywords`` 等

    Note:
        过期清理在实现侧通过 ``prune_expired()`` 完成，不在本 Protocol 中声明。
    """

    def search_relevant(
        self, query: str, limit: int = 10, recent_minutes: int = 0
    ) -> list[Any]: ...
    def index_entry(self, session_key: str, entry: Any) -> None: ...
    def save(self) -> None: ...
    def get_stats(self) -> dict[str, Any]: ...


class OnThinkingCallback(Protocol):
    """思考流式输出回调。

    由 ``miniagent.core.thinking_callback.invoke_on_thinking`` 调用；
    若签名含 ``full_record`` / ``reset`` / ``is_last_step`` 或 ``**kwargs``，会按需传入。
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
    """工具执行完成回调。

    参数：工具名、参数 JSON、结果文本、是否成功；可选 ``thinking_header`` 关键字参数。
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


# (tool_name, args_json, result_or_message) — 同步，在工具执行后立即触发
OnToolCall = Callable[[str, str, str], None]
# 结构化计划确认：异步返回 ConfirmationResult
OnPlan = Callable[[StructuredPlan], Awaitable[ConfirmationResult]]
OnThinking = OnThinkingCallback
OnToolFinish = OnToolFinishCallback


# ============================================================================
# RuntimeContext 相关 Protocol
# ============================================================================


@runtime_checkable
class UnifiedEngineProtocol(Protocol):
    """统一引擎接口协议。

    用于 ``RuntimeContext.engine`` 字段类型。

    实现类：``miniagent.engine.engine.UnifiedEngine``

    Methods:
        run_agent_with_thinking: 主对话入口（带思考显示）
        inject_message: 向会话历史注入用户消息
        get_thinking_display: 返回 ``ThinkingDisplay`` 实例

    Note:
        线性工具管线 ``run_pipeline`` 为独立函数，见 ``miniagent.core.agent.run_pipeline``，
        不属于引擎实例方法。
    """

    async def run_agent_with_thinking(
        self,
        user_input: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any: ...

    def inject_message(
        self,
        session_key: str,
        message: str,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...

    def get_thinking_display(self) -> Any: ...


@runtime_checkable
class ChannelRouterProtocol(Protocol):
    """通道路由器接口协议。

    将 CLI / 飞书私聊 / 飞书群聊通道映射到会话 ID。

    实现类：``miniagent.infrastructure.channel_router.ChannelRouter``
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


@runtime_checkable
class MessageQueueProtocol(Protocol):
    """消息队列管理器接口协议。

    为每个 ``chat_id`` 维护独立队列；CLI 使用 ``"__cli__"``。

    实现类：``miniagent.infrastructure.message_queue.MessageQueueManager``
    """

    exec_lock: Any | None

    async def dispatch(
        self,
        chat_id: str,
        coro: Any,
        on_start: Any = None,
        on_done: Any = None,
    ) -> None: ...

    def abort_chat(self, chat_id: str) -> dict[str, Any]: ...
    def get_status(self) -> dict[str, Any]: ...
    def get_agent_status(self, chat_id: str | None = None) -> dict[str, Any]: ...


@runtime_checkable
class FeishuRuntimeProtocol(Protocol):
    """飞书 WebSocket 运行时接口协议。

    实现类：``miniagent.engine.feishu_state.FeishuRuntime``
    """

    def start(
        self,
        create_handler: Any,
        state: dict | None = None,
        *,
        user_status: Callable[[str], None] | None = None,
    ) -> None: ...

    def stop(self) -> None: ...
    def is_running(self) -> bool: ...


__all__ = [
    "MemoryStoreProtocol",
    "SessionManagerProtocol",
    "ToolRegistryProtocol",
    "ToolMonitorProtocol",
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "OnThinkingCallback",
    "OnToolFinishCallback",
    "OnToolCall",
    "OnPlan",
    "OnThinking",
    "OnToolFinish",
    "UnifiedEngineProtocol",
    "ChannelRouterProtocol",
    "MessageQueueProtocol",
    "FeishuRuntimeProtocol",
]
