"""Mini Agent Python — 工具系统与上下文管理类型

核心类型涵盖：
- 工具定义（ToolDefinition）与注册表（ToolRegistryProtocol）
- 工具执行上下文（ToolContext）与结果（ToolResult）
- 权限级别（ToolPermission）
- 工具箱（Toolbox）：粗粒度能力分组
- 上下文管理：Token 估算、上下文压缩

**权限模型（两层，勿混淆）**：

- ``ToolDefinition.permission``：工具元数据，描述该工具的安全级别与是否需要用户确认。
  ``require-confirm`` 由 :func:`miniagent.agent.executor.execute_plan` 在调用 handler 前
  经 ``ConfirmationChannel`` 统一拦截（除非 ``AgentConfig.auto_execute_confirmed``）。
- ``ToolContext.permission``：单次执行时的运行时沙箱策略，由 executor 注入。
  各 handler（如 ``exec_command``）据此决定是否启用路径沙箱 / 命令 allowlist 检查。
  工具级 ``sandbox`` / ``allowlist`` 标签**不会**自动改写 ``ctx.permission``。

**类型注解注意**：``ToolRegistryProtocol`` 含成员方法 ``list``；在其体内若将返回值写为内建泛型 ``list[T]``，
mypy 会将 ``list`` 解析为该方法而非类型构造器并报 ``valid-type``。故协议中与 ``list`` 相邻的列表返回
注解使用 ``builtins.list[...]``。长期若重命名 API 为 ``list_tool_names`` 等，可再统一改为小写 ``list`` 泛型。

**Protocol 最佳实践**：
- Protocol 不使用 @abstractmethod（Python Protocol 仅定义方法签名）
- 使用 @runtime_checkable 支持 isinstance() 检查
- 默认实现：:class:`miniagent.agent.tools.registry.DefaultToolRegistry`、
  :class:`miniagent.agent.context.DefaultContextManager`

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
    from miniagent.agent.ports.knowledge import KnowledgeRegistryProtocol
    from miniagent.agent.types.skill import ClawHubClientProtocol

ChatCompletionMessageParam = dict[str, Any]
ChatCompletionToolParam = dict[str, Any]

# ============================================================================
# 权限与工具箱
# ============================================================================

# 工具权限级别（ToolDefinition.permission 语义）：
# - sandbox：文件/路径类工具；handler 内通常配合 ctx.permission 做路径沙箱检查
# - allowlist：不依赖路径沙箱的外部 API / 只读工具（如飞书、定时任务查询）
# - require-confirm：executor 执行 handler 前须经 ConfirmationChannel 用户确认
ToolPermission = Literal["sandbox", "allowlist", "require-confirm"]

# ToolContext.permission 运行时策略（与 ToolDefinition.permission 独立）
ToolRuntimePermission = Literal["sandbox", "allowlist", "full"]


@dataclass
class Toolbox:
    """工具箱：粗粒度的能力分组

    与 ``miniagent.assistant.skills.builtin_toolboxes.BUILTIN_TOOLBOXES`` 及技能注册表中的
    toolbox 元数据对齐；Phase 1 规划器返回 ``required_toolboxes``，Phase 2 经
    ``ToolRegistryProtocol.get_schemas_by_toolboxes`` 筛选可见工具。
    ``ToolDefinition.toolbox=None`` 表示核心能力，始终包含在筛选结果中。

    Attributes:
        id: 工具箱唯一标识（与 ToolDefinition.toolbox 字段对应）
        name: 显示名称
        description: 能力描述（供 LLM 理解）
        keywords: 关键词，用于语义匹配与任务分类
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
    """工具执行上下文（运行时沙箱与集成依赖，由 executor 构造）

    ``permission`` 控制**本次调用**的沙箱/命令策略，与 ``ToolDefinition.permission``
    （工具元数据）独立。executor 默认注入 ``permission="allowlist"``，使 ``exec_command``
    等跳过 sandbox 三层检查；路径类工具仍通过 ``resolve_path_for_tool`` 校验
    ``allowed_paths``。

    Attributes:
        cwd: 当前工作目录
        allowed_paths: 允许访问的路径列表（路径类工具在 sandbox 策略下生效）
        permission: 运行时沙箱策略（``sandbox`` / ``allowlist`` / ``full``；``full`` 仅测试/调试）
        clawhub: ClawHub 客户端（可选；由 ApplicationContainer 注入）
        knowledge_registry: 知识库注册表（由 ApplicationContainer 注入）
        session_key: 当前 Agent 会话键（可选；由执行器注入供会话级只读工具使用）
        cli_loop_state: CLI 主循环共享状态（可选；点命令工具用）
        cli_dispatch_allow_mutations: 是否允许 dispatch 在 capture 下执行会话变异子命令
        message_queue_abort_chat_id: 飞书当前 ``chat_id``；供 ``run_dot_command`` 执行 ``.abort`` 等
        feishu_im_receive_id_type: 飞书发消息时的 ``receive_id_type``（``chat_id`` / ``open_id`` / ``union_id``）
        feishu_im_receive_id: 当 ``receive_id_type`` 为 ``open_id`` / ``union_id`` 时的默认 ``receive_id``
    """

    cwd: str
    allowed_paths: list[str] = field(default_factory=list)
    permission: ToolRuntimePermission = "sandbox"
    clawhub: ClawHubClientProtocol | None = None
    knowledge_registry: KnowledgeRegistryProtocol | None = None
    llm_client: Any | None = None
    session_key: str | None = None
    cli_loop_state: dict[str, Any] | None = None
    cli_dispatch_allow_mutations: bool = True
    message_queue_abort_chat_id: str | None = None
    feishu_im_receive_id_type: str | None = None
    feishu_im_receive_id: str | None = None


@dataclass
class ToolResult:
    """工具执行结果

    Attributes:
        success: 是否成功
        content: 结果内容（展示给 LLM / 用户的文本）
        meta: 额外元数据。常见键：
            - ``error_type``：异常类名（如 ``TimeoutError``、``PermissionError``、
              ``ConfirmationRequired``、``ConfirmationRejected``）
            - ``bytes`` / ``lines``：文件类工具统计
            - 其他工具自定义结构化字段

    Note:
        executor 与 monitor 主要读取 ``success`` / ``content``；``meta`` 供日志与诊断。
    """

    success: bool
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


# 工具处理器签名：接收 (args: dict, ctx: ToolContext) 并返回 ToolResult 协程。
# 实际注册的工具函数必须是 async def；sync handler 会在 await 时失败。
ToolHandler = Callable[[dict[str, Any], "ToolContext"], Awaitable["ToolResult"]]


@dataclass
class ToolDefinition:
    """工具定义：包含 schema、处理器、权限和帮助信息

    Attributes:
        schema: OpenAI tool_call schema（``ChatCompletionToolParam``）
        handler: 异步工具处理器（``ToolHandler``）
        permission: 工具元数据权限（见模块 docstring 权限模型说明）
        help_text: 帮助文本（``/help``、确认提示等）
        toolbox: 所属工具箱 ID；``None`` 表示核心能力，toolbox 筛选时始终包含
    """

    schema: ChatCompletionToolParam
    handler: ToolHandler
    permission: ToolPermission
    help_text: str
    toolbox: str | None = None


@dataclass
class RegisteredTool(ToolDefinition):
    """已注册的工具（在 ToolDefinition 基础上增加名称）

    ``name`` 由 :meth:`miniagent.agent.tools.registry.DefaultToolRegistry.register`
    赋值；注册前不应手动构造带空 name 的实例。

    Attributes:
        name: 工具名称（注册时指定，与 schema.function.name 一致）
    """

    name: str = ""


@runtime_checkable
class ToolRegistryProtocol(Protocol):
    """工具注册表接口协议

    管理工具的注册、注销、查询和按工具箱筛选。

    默认实现：:class:`miniagent.agent.tools.registry.DefaultToolRegistry`
    用于 ``ApplicationContainer.registry`` 字段，支持依赖注入。

    实现约定：
    - ``register``：同名重复注册应抛出 ``ValueError``
    - ``get_all``：应返回内部字典的副本，避免外部篡改
    - ``get_schemas_by_toolboxes([])`` / ``get_by_toolboxes([])``：空列表表示不筛选，返回全部
    - toolbox 筛选：`toolbox is None` 的工具始终包含（核心能力）
    """

    def register(self, name: str, tool: ToolDefinition) -> None:
        """注册一个工具

        Args:
            name: 工具名称
            tool: 工具定义

        Raises:
            ValueError: 实现类在名称已存在时应抛出
        """
        ...

    def unregister(self, name: str) -> bool:
        """注销一个工具

        Args:
            name: 工具名称

        Returns:
            是否成功注销（不存在时返回 False）
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
        """获取所有工具（实现类应返回副本）

        Returns:
            工具名称到工具对象的映射
        """
        ...

    def get_schemas(self) -> builtins.list[ChatCompletionToolParam]:
        """获取所有工具的 OpenAI schema

        Returns:
            OpenAI tool schema 列表（注册顺序）
        """
        ...

    def list(self) -> builtins.list[str]:
        """获取所有工具名称

        Returns:
            工具名称列表（注册顺序）
        """
        ...

    def get_schemas_by_toolboxes(
        self, ids: Sequence[str]
    ) -> builtins.list[ChatCompletionToolParam]:
        """按工具箱筛选，返回 schema 列表

        Args:
            ids: 工具箱 ID 列表；空序列时返回全部 schema

        Returns:
            匹配的工具 schema 列表（含 ``toolbox=None`` 的核心工具）
        """
        ...

    def get_by_toolboxes(self, ids: Sequence[str]) -> dict[str, RegisteredTool]:
        """按工具箱筛选，返回完整工具对象

        Args:
            ids: 工具箱 ID 列表；空序列时等价于 ``get_all()``

        Returns:
            匹配的工具名称到工具对象的映射
        """
        ...


# ============================================================================
# 上下文管理
# ============================================================================


@dataclass
class TokenEstimate:
    """单段文本的 token 估算结果

    由 :func:`miniagent.agent.context.estimate_token_estimate` 产生；
    :func:`miniagent.agent.context.estimate_tokens` 返回其中的 ``tokens`` 字段。

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

    默认实现：:class:`miniagent.agent.context.DefaultContextManager`
    在 token 超过阈值时执行压缩；``append`` 可能抛出
    :class:`miniagent.agent.context.ContextBudgetExceeded`（overflow_strategy=error）。

    实现扩展（非协议必需）：``set_tools``、``try_redact_oldest_tool_message_once`` 等。
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

        Raises:
            ContextBudgetExceeded: overflow_strategy 为 error 且超预算时
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

    def get_token_report(self) -> str:
        """获取当前 token 使用报告

        Returns:
            Token 使用报告字符串
        """
        ...

    def get_messages(self) -> list[ChatCompletionMessageParam]:
        """获取当前消息列表

        Returns:
            消息列表（实现类通常返回副本）
        """
        ...


__all__ = [
    "ToolPermission",
    "ToolRuntimePermission",
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
