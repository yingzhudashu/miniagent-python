"""Mini Agent Python — Protocol 类型定义

定义核心接口的 Protocol 类型，替代 Any 类型，提升类型安全性。

使用场景：
- ``runtime/context.py`` RuntimeContext 字段类型
- ``core/executor.py`` 回调函数签名
- ``core/agent.py`` 参数类型

注意：Protocol 仅用于类型检查，不影响运行时行为。

**Protocol 定义位置**：
- MemoryStoreProtocol: ``miniagent/types/memory.py``（本模块再导出以便于导入）
- SessionManagerProtocol: ``miniagent/types/memory.py``
- ToolRegistryProtocol: ``miniagent/types/tool.py``
- ToolMonitorProtocol: ``miniagent/types/agent.py``
- SkillRegistryProtocol: ``miniagent/types/skill.py``
- ClawHubClientProtocol: ``miniagent/types/skill.py``
- 本模块仅定义运行时注入特有的 Protocol（ActivityLog、KeywordIndex、回调等）
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from miniagent.types.agent import ToolMonitorProtocol

# 从其他模块再导出Protocol，便于统一导入
from miniagent.types.memory import MemoryStoreProtocol, SessionManagerProtocol
from miniagent.types.tool import ToolRegistryProtocol


@runtime_checkable
class ActivityLogProtocol(Protocol):
    """活动日志接口协议

    定义日志记录方法，用于追踪会话活动。

    Methods:
        log_session_start: 记录会话开始
        log_llm_call: 记录 LLM 调用
        log_tool_call: 记录工具调用
        log_final_reply: 记录最终回复
        get_stats: 获取活动统计（新增）
        clear_old_entries: 清理旧条目（新增）

    Example:
        >>> log: ActivityLogProtocol = get_activity_log()
        >>> log.log_session_start("session-1", "你好", "cli")
        >>> stats = log.get_stats()

    Note:
        - 所有日志方法为同步调用
        - get_stats() 返回字典，包含日志条目数量等统计
        - clear_old_entries() 用于定期清理过期日志

    See Also:
        - miniagent.memory.activity_log: ActivityLog 实现
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

    # ── 新增方法 ──
    def get_stats(self) -> dict[str, Any]:
        """获取活动日志统计信息

        Returns:
            dict: 包含以下键的统计字典：
                - total_entries: 总条目数
                - sessions: 会话数量
                - date_range: 日志日期范围
                - last_updated: 最后更新时间

        Example:
            >>> stats = log.get_stats()
            >>> print(stats["total_entries"])
        """
        ...

    def clear_old_entries(self, days: int = 30) -> int:
        """清理超过指定天数的旧条目

        Args:
            days: 保留天数，默认 30 天

        Returns:
            int: 清理的条目数量

        Example:
            >>> removed = log.clear_old_entries(days=7)
            >>> print(f"清理了 {removed} 条旧日志")
        """
        ...


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
    """统一引擎接口协议

    定义引擎核心方法，用于 RuntimeContext.engine 字段类型。

    Methods:
        run_agent_with_thinking: 运行 Agent 并处理思考输出
        inject_message: 注入消息到会话
        run_pipeline: 运行完整管线（新增）
        get_thinking_display: 获取思考显示器（新增）

    Example:
        >>> engine: UnifiedEngineProtocol = ctx.engine
        >>> result = await engine.run_agent_with_thinking("你好", registry, monitor, session_manager)

    Note:
        - run_agent_with_thinking 是主入口，用于对话交互
        - run_pipeline 用于嵌入调用场景
        - get_thinking_display 返回 ThinkingDisplay 实例

    See Also:
        - miniagent.engine.engine: UnifiedEngine 实现
        - docs/ARCHITECTURE.md: 引擎层架构
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

    # ── 新增方法 ──
    async def run_pipeline(
        self,
        user_input: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """运行完整管线（嵌入调用入口）

        与 run_agent_with_thinking 不同，此方法用于嵌入调用场景，
        不处理思考显示，直接返回 Agent 结果。

        Args:
            user_input: 用户输入
            *args: 可变参数
            **kwargs: 关键字参数（可含 registry、monitor 等）

        Returns:
            Agent 执行结果

        Example:
            >>> result = await engine.run_pipeline("帮我整理文件")
        """
        ...

    def get_thinking_display(self) -> Any:
        """获取思考显示器实例

        Returns:
            ThinkingDisplay 实例，用于控制思考输出显示

        Example:
            >>> thinking = engine.get_thinking_display()
            >>> thinking.clear()
        """
        ...


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
    "SessionManagerProtocol",
    "ToolRegistryProtocol",
    "ToolMonitorProtocol",
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "OnThinkingCallback",
    "OnToolFinishCallback",
    "UnifiedEngineProtocol",
    "ChannelRouterProtocol",
    "MessageQueueProtocol",
    "FeishuRuntimeProtocol",
]