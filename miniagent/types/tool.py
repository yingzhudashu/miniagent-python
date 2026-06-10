"""Mini Agent Python — 工具系统与上下文管理类型

核心类型涵盖：
- 工具定义（ToolDefinition）与注册表（ToolRegistryProtocol）
- 工具执行上下文（ToolContext）与结果（ToolResult）
- 权限级别（ToolPermission）
- 工具箱（Toolbox）：粗粒度能力分组
- 上下文管理：Token 估算、上下文压缩

**类型注解注意**：``ToolRegistryProtocol`` 含成员方法 ``list``；在其体内若将返回值写为内建泛型 ``list[T]``，
mypy 会将 ``list`` 解析为该方法而非类型构造器并报 ``valid-type``。故协议中与 ``list`` 相邻的列表返回
注解使用 ``typing.List[...]``（或 ``from __future__ import annotations`` 下仍须避免与方法名同形的
``list[...]`` 出现在该 Protocol 块内）。长期若重命名 API 为 ``list_tool_names`` 等，可再统一改为小写 ``list`` 泛型。

**Protocol 最佳实践**：
- Protocol 不使用 @abstractmethod（Python Protocol 仅定义方法签名）
- 使用 @runtime_checkable 支持 isinstance() 检查

**类型改进**：
- clawhub 字段使用 ClawHubClientProtocol 替代 Any
- cli_loop_state 字段使用 CliLoopState 替代 Any
"""

from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from miniagent.engine.cli_state import CliLoopState
    from miniagent.types.skill import ClawHubClientProtocol

from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

# ============================================================================
# 权限与工具箱
# ============================================================================

# 工具权限级别：
# - sandbox：在 allowed_paths 内受沙箱约束
# - allowlist：仅允许预置安全命令清单
# - require-confirm：执行前需用户确认（若上层实现该流程）
ToolPermission = Literal["sandbox", "allowlist", "require-confirm"]


@dataclass
class Toolbox:
    """工具箱：粗粒度的能力分组

    Attributes:
        id: 工具箱唯一标识
        name: 显示名称
        description: 能力描述（供 LLM 理解）
        keywords: 关键词，用于语义匹配
    """

    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)


# ============================================================================
# 工具执行
# ============================================================================


@dataclass
class ToolContext:
    """工具执行上下文

    Attributes:
        cwd: 当前工作目录
        allowed_paths: 允许访问的路径列表（sandbox 模式下生效）
        permission: 权限级别
        clawhub: ClawHub 客户端（可选；由 RuntimeContext 注入，技能搜索/安装工具优先使用）
        session_key: 当前 Agent 会话键（可选；由执行器注入供会话级只读工具使用）
        cli_loop_state: 与 unified_main 共享的状态 dict（可选；点命令工具用）
        cli_dispatch_allow_mutations: 是否允许 dispatch 在 capture 下执行会话变异子命令
        message_queue_abort_chat_id: 飞书当前 ``chat_id``；供 ``run_dot_command`` 执行 ``.abort`` 等时传给 ``dispatch_command``
        feishu_im_receive_id_type: 飞书发消息时的 ``receive_id_type``（``chat_id`` / ``open_id`` / ``union_id``）；缺省由执行器读环境变量
        feishu_im_receive_id: 当 ``receive_id_type`` 为 ``open_id`` / ``union_id`` 时用于 ``create`` 的默认 ``receive_id``（通常为入站 ``sender_id``）
    """

    cwd: str
    allowed_paths: list[str] = field(default_factory=list)
    permission: ToolPermission = "sandbox"
    clawhub: ClawHubClientProtocol | None = None  # 类型改进：使用 Protocol 替代 Any
    session_key: str | None = None
    cli_loop_state: CliLoopState | None = None  # 类型改进：使用具体类型替代 Any
    cli_dispatch_allow_mutations: bool = True
    message_queue_abort_chat_id: str | None = None
    feishu_im_receive_id_type: str | None = None
    feishu_im_receive_id: str | None = None


@dataclass
class ToolResult:
    """工具执行结果

    Attributes:
        success: 是否成功
        content: 结果内容
        meta: 额外元数据（可选）
    """

    success: bool
    content: str
    meta: dict[str, Any] = field(default_factory=dict)



# ============================================================================
# 工具定义与注册表
# ============================================================================


# ============================================================================
# 工具处理器类型别名
# ============================================================================

# 工具处理器签名：接收 (args: dict, ctx: ToolContext) 并返回 ToolResult 协程。
# 实际注册的工具函数必须是 async def，返回 ToolResult。
ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable["ToolResult"]]


@dataclass
class ToolDefinition:
    """工具定义：包含 schema、处理器、权限和帮助信息

    Attributes:
        schema: OpenAI tool_call schema
        handler: 工具处理器
        permission: 权限级别
        help_text: 帮助文本
        toolbox: 所属工具箱 ID。未设置则始终包含（核心能力）
    """

    schema: ChatCompletionToolParam
    handler: ToolHandler
    permission: ToolPermission
    help_text: str
    toolbox: str | None = None


@dataclass
class RegisteredTool(ToolDefinition):
    """已注册的工具（在 ToolDefinition 基础上增加名称）

    Attributes:
        name: 工具名称（注册时指定）
    """

    name: str = ""


@runtime_checkable
class ToolRegistryProtocol(Protocol):
    """工具注册表接口协议

    管理工具的注册、注销、查询和按工具箱筛选。

    该 Protocol 用于 ``miniagent.runtime.context.RuntimeContext`` 的
    registry 字段类型，支持依赖注入模式。
    """

    def register(self, name: str, tool: ToolDefinition) -> None:
        """注册一个工具

        Args:
            name: 工具名称
            tool: 工具定义
        """
        ...

    def unregister(self, name: str) -> bool:
        """注销一个工具

        Args:
            name: 工具名称

        Returns:
            是否成功注销
        """
        ...

    def get(self, name: str) -> RegisteredTool | None:
        """查询单个工具

        Args:
            name: 工具名称

        Returns:
            已注册的工具，若不存在则返回 None
        """
        ...

    def get_all(self) -> dict[str, RegisteredTool]:
        """获取所有工具

        Returns:
            工具名称到工具对象的映射
        """
        ...

    def get_schemas(self) -> builtins.list[ChatCompletionToolParam]:
        """获取所有工具的 OpenAI schema

        Returns:
            OpenAI tool schema 列表
        """
        ...

    def list(self) -> builtins.list[str]:
        """获取所有工具名称

        Returns:
            工具名称列表
        """
        ...

    def get_schemas_by_toolboxes(
        self, ids: Sequence[str]
    ) -> builtins.list[ChatCompletionToolParam]:
        """按工具箱筛选，返回 schema 列表

        Args:
            ids: 工具箱 ID 列表

        Returns:
            匹配的工具 schema 列表
        """
        ...

    def get_by_toolboxes(self, ids: Sequence[str]) -> dict[str, RegisteredTool]:
        """按工具箱筛选，返回完整工具对象

        Args:
            ids: 工具箱 ID 列表

        Returns:
            匹配的工具名称到工具对象的映射
        """
        ...


# ============================================================================
# 上下文管理
# ============================================================================


@dataclass
class TokenEstimate:
    """消息的 token 估算结果

    Attributes:
        tokens: 估算的 token 数
        char_length: 原始字符长度
    """

    tokens: int
    char_length: int


@dataclass
class ContextState:
    """上下文状态：跟踪当前消息列表的 token 使用

    Attributes:
        messages: 当前消息列表
        total_tokens: 当前估算的总 token 数
        compressed: 是否已被压缩过
    """

    messages: list[ChatCompletionMessageParam]
    total_tokens: int
    compressed: bool


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """上下文管理器接口协议

    负责 Token 估算、上下文压缩和消息窗口维护。

    该 Protocol 用于管理对话上下文的 token 使用量，
    并在超过阈值时执行压缩策略。
    """

    def get_state(self) -> ContextState:
        """获取当前上下文状态

        Returns:
            上下文状态对象
        """
        ...

    def init(self, system_prompt: str, user_input: str) -> None:
        """初始化消息（system + user）

        Args:
            system_prompt: 系统提示词
            user_input: 用户输入
        """
        ...

    def append(self, msg: ChatCompletionMessageParam) -> None:
        """追加消息并检查是否需要压缩

        Args:
            msg: 消息对象
        """
        ...

    def needs_compression(self) -> bool:
        """检查是否需要压缩

        Returns:
            是否需要压缩
        """
        ...

    def compress(self) -> None:
        """执行压缩（保留首尾，中间摘要）"""
        ...

    def inject_memory(self, memory: SessionMemory | None) -> None:
        """兼容旧路径：将记忆摘要追加到当前上下文。

        执行阶段主路径已改为由 executor 把动态记忆放入 current turn user
        context；实现类保留该方法仅用于旧调用兼容。

        Args:
            memory: 会话记忆对象（可选）
        """
        ...

    def get_token_report(self) -> str:
        """获取当前 token 使用报告

        Returns:
            Token 使用报告字符串
        """
        ...

    def get_messages(self) -> list[ChatCompletionMessageParam]:
        """获取当前消息列表

        Returns:
            消息列表
        """
        ...


# Forward reference fix
from miniagent.types.memory import SessionMemory  # noqa: E402

__all__ = [
    "ToolPermission",
    "Toolbox",
    "ToolContext",
    "ToolResult",
    "ToolHandler",
    "ToolDefinition",
    "RegisteredTool",
    "ToolRegistryProtocol",
    "TokenEstimate",
    "ContextState",
    "ContextManagerProtocol",
]
