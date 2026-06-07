"""Mini Agent Python — 模型与 Agent 配置类型

三层配置体系：
- ModelConfig：模型层（API 端点、temperature、thinking 等）
- AgentConfig：Agent 层（max_turns、tool_timeout、上下文策略等）
- 分组配置：SessionBindingConfig、FeishuChannelConfig 按职责分组，降低管理复杂度

配置分组说明：
- SessionBindingConfig：会话相关字段（session_key、session_workspace、conversation_history 等）
- FeishuChannelConfig：飞书通道相关字段（receive_chat_id、trigger_message_id 等）

向后兼容：
- AgentConfig 同时支持分组结构和平铺字段
- 旧的平铺字段输入仍可使用，但推荐使用分组结构

设计背景见 docs/ARCHITECTURE.md § 配置层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.types.tool import Toolbox, ToolRegistryProtocol

# 数据类占位默认值（与 config.defaults.json 对齐；运行时请用 get_default_*_config()）
_MAX_TURNS_DEFAULT = 400
_TOOL_TIMEOUT_DEFAULT = 60


# ============================================================================
# ModelConfig — 模型层配置
# ============================================================================


@dataclass
class ModelConfig:
    """模型配置

    运行时默认值由 ``get_default_model_config()`` 从 ``config.defaults.json`` 加载；
    下列字段仅为直接构造 ``ModelConfig()`` 时的占位，勿与 JSON / 环境变量混为一谈。

    Attributes:
        base_url: API 端点
        model: 模型名称
        temperature: 温度（0.0-2.0）
        top_p: top_p 采样（0.0-1.0）
        max_tokens: 最大输出 token 数
        thinking_level: thinking 级别（none/light/medium/high）
        thinking_budget: thinking token 预算
        context_window: 上下文窗口大小（token）
        retry_count: API 调用重试次数

    Example:
        >>> config = ModelConfig(model="gpt-4o", temperature=0.7)
        >>> config.model  # "gpt-4o"

    Note:
        - 运行时推荐使用 get_default_model_config() 获取完整配置
        - thinking_level 控制模型思考深度
        - thinking_budget 仅在 thinking_level 非 none 时生效
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    thinking_level: str = "light"
    thinking_budget: int = 1024
    context_window: int = 128000
    retry_count: int = 2


# ============================================================================
# SessionBindingConfig — 会话绑定配置分组
# ============================================================================


@dataclass
class SessionBindingConfig:
    """会话绑定配置

    将会话相关字段集中管理，降低 AgentConfig 的字段数量，提高配置的可读性和维护性。

    Attributes:
        session_key: 会话记忆键（用于加载/保存会话记忆）
        session_workspace: 会话工作空间路径（工具操作的默认目录）
        session_registry: 会话级工具注册表（可选，用于会话隔离的工具管理）
        session_toolboxes: 会话工具箱列表（会话级别加载的工具箱）
        conversation_history: 对话历史（跨轮次保留，传递给 LLM 的消息序列）

    Example:
        >>> session_cfg = SessionBindingConfig(
        >>>     session_key="session-123",
        >>>     session_workspace="/workspaces/sessions/session-123/files"
        >>> )

    Note:
        - session_key 与磁盘存储路径关联
        - conversation_history 会随对话增长，需注意 token 预算
        - session_registry 允许会话拥有独立的工具集

    See Also:
        - AgentConfig: Agent 主配置
        - miniagent.session.manager: 会话管理实现
        - docs/MEMORY_SYSTEM.md: 会话与记忆架构
    """

    session_key: str | None = None
    session_workspace: str | None = None
    session_registry: ToolRegistryProtocol | None = None
    session_toolboxes: list[Toolbox] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)


# ============================================================================
# FeishuChannelConfig — 飞书通道配置分组
# ============================================================================


@dataclass
class FeishuChannelConfig:
    """飞书通道配置

    将飞书相关字段集中管理，便于飞书场景的配置传递，降低 AgentConfig 的字段数量。

    Attributes:
        receive_chat_id: 飞书 API 用 chat_id（如 oc_xxx）
        trigger_message_id: 触发本轮的飞书入站 message_id（可选）
        root_id: 入站事件 root_id（话题根消息，可选）
        parent_id: 入站事件 parent_id（可选）
        thread_id: 入站事件 thread_id（话题上下文，可选）
        im_receive_id_type: 飞书 IM create 消息的 receive_id_type（chat_id/open_id/union_id）
        im_receive_id: 非 chat_id 时作为默认 receive_id（通常为入站发送者 open_id）
        cli_loop_state: CLI/飞书共享的 CliLoopState dict（供 run_dot_command 调用 dispatch_command）
        cli_dispatch_allow_mutations: capture 模式下是否允许修改共享状态的子命令

    Example:
        >>> feishu_cfg = FeishuChannelConfig(
        >>>     receive_chat_id="oc_abc123",
        >>>     trigger_message_id="om_xyz789"
        >>> )

    Note:
        - receive_chat_id 注入工具上下文，使 .abort 等命令作用于当前群队列
        - cli_dispatch_allow_mutations 飞书默认 False，避免意外修改状态
        - MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时 cli_dispatch_allow_mutations 为 True

    See Also:
        - AgentConfig: Agent 主配置
        - miniagent.feishu.poll_server: 飞书 WebSocket 实现
        - docs/FEISHU.md: 飞书集成文档
    """

    receive_chat_id: str | None = None
    trigger_message_id: str | None = None
    root_id: str | None = None
    parent_id: str | None = None
    thread_id: str | None = None
    im_receive_id_type: str | None = None
    im_receive_id: str | None = None
    cli_loop_state: Any | None = None
    cli_dispatch_allow_mutations: bool = True


# ============================================================================
# AgentConfig — Agent 层配置
# ============================================================================


@dataclass
class AgentConfig:
    """Agent 配置（分组版）

    配置合并优先级（从低到高）：
    1. get_default_agent_config() — 默认值
    2. run_agent(options.agent_config) — 用户传入
    3. plan.suggested_config — 规划器推荐

    配置分组说明：
    - 核心配置：max_turns、tool_timeout、http_timeout 等基础参数
    - 上下文配置：context_reserve_ratio、compress_threshold 等上下文策略
    - 输出配置：response_language、response_format 等输出格式
    - 会话配置：SessionBindingConfig 嵌套对象（推荐）
    - 飞书配置：FeishuChannelConfig 嵌套对象（推荐）
    - 调试配置：debug、log_token_usage、log_file 等调试选项
    - 高级配置：tool_selection_strategy、loop_detection 等高级选项

    向后兼容说明：
    - 旧的平铺字段仍可使用（session_key、feishu_receive_chat_id 等）
    - 新的分组结构（session_config、feishu_config）提供更好的组织性
    - 推荐使用分组结构，平铺字段将在未来版本逐步弃用

    Attributes:
        max_turns: 最大轮数（ReAct loop 迭代次数；默认 400）
        tool_timeout: 工具超时（秒，默认 60）
        http_timeout: HTTP 超时（秒，默认 120）
        context_reserve_ratio: 上下文保留比例（默认 0.15）
        context_compress_threshold: 上下文触发压缩的阈值比例（默认 0.6）
        context_overflow_strategy: 上下文溢出处理策略（summarize/truncate/error）
        compress_messages: 是否压缩消息
        tool_selection_strategy: 工具选择策略（all/toolbox/auto）
        auto_execute_confirmed: 是否自动确认执行
        allow_parallel_tools: 是否允许并行工具调用
        response_language: 响应语言（默认 zh-CN）
        response_format: 响应格式（text/markdown/structured）
        debug: 调试模式
        log_token_usage: 是否记录 token 用量
        log_file: 增量日志文件路径
        loop_detection: 循环检测配置
        model_overrides: 模型覆盖
        session_config: 会话绑定配置（分组结构，推荐）
        feishu_config: 飞书通道配置（分组结构，推荐）
        risk_level: 风险等级（low/medium/high）
        history_progressive_compression: 是否启用磁盘会话历史的渐进式压缩

    平铺字段（向后兼容，逐步弃用）：
        session_key: 会话记忆键（推荐使用 session_config.session_key）
        session_workspace: 会话工作空间路径（推荐使用 session_config.session_workspace）
        session_registry: 会话级工具注册表（推荐使用 session_config.session_registry）
        session_toolboxes: 会话工具箱列表（推荐使用 session_config.session_toolboxes）
        conversation_history: 对话历史（推荐使用 session_config.conversation_history）
        cli_loop_state: CLI/飞书共享状态（推荐使用 feishu_config.cli_loop_state）
        cli_dispatch_allow_mutations: capture 模式权限（推荐使用 feishu_config.cli_dispatch_allow_mutations）
        feishu_receive_chat_id: 飞书 chat_id（推荐使用 feishu_config.receive_chat_id）
        feishu_trigger_message_id: 飞书 message_id（推荐使用 feishu_config.trigger_message_id）
        feishu_root_id: 飞书 root_id（推荐使用 feishu_config.root_id）
        feishu_parent_id: 飞书 parent_id（推荐使用 feishu_config.parent_id）
        feishu_thread_id: 飞书 thread_id（推荐使用 feishu_config.thread_id）
        feishu_im_receive_id_type: 飞书 receive_id_type（推荐使用 feishu_config.im_receive_id_type）
        feishu_im_receive_id: 飞书 receive_id（推荐使用 feishu_config.im_receive_id）

    Example:
        >>> # 推荐的分组结构
        >>> config = AgentConfig(
        >>>     max_turns=400,
        >>>     session_config=SessionBindingConfig(session_key="session-1"),
        >>>     feishu_config=FeishuChannelConfig(receive_chat_id="oc_abc")
        >>> )
        >>>
        >>> # 向后兼容的平铺结构（仍可使用）
        >>> config = AgentConfig(
        >>>     max_turns=400,
        >>>     session_key="session-1",
        >>>     feishu_receive_chat_id="oc_abc"
        >>> )

    Note:
        - 核心模块覆盖率目标 ≥95%，整体 ≥85%
        - 分组结构使配置更清晰，降低维护复杂度
        - 平铺字段将在未来版本标记为 deprecated

    See Also:
        - SessionBindingConfig: 会话绑定配置详细说明
        - FeishuChannelConfig: 飞书通道配置详细说明
        - miniagent.core.config: 配置合并实现
        - docs/ARCHITECTURE.md: 配置层架构说明
    """

    # ── 核心 Agent 配置 ──
    max_turns: int = _MAX_TURNS_DEFAULT
    tool_timeout: int = _TOOL_TIMEOUT_DEFAULT
    http_timeout: int = 120
    allow_parallel_tools: bool = True
    auto_execute_confirmed: bool = False

    # ── 上下文管理配置 ──
    context_reserve_ratio: float = 0.15
    context_compress_threshold: float = 0.6
    context_overflow_strategy: str = "summarize"  # "summarize" | "truncate" | "error"
    compress_messages: bool = True

    # ── 输出与格式配置 ──
    response_language: str = "zh-CN"
    response_format: str = "markdown"  # "text" | "markdown" | "structured"

    # ── 会话绑定配置（分组结构，推荐）──
    session_config: SessionBindingConfig | None = None

    # ── 飞书通道配置（分组结构，推荐）──
    feishu_config: FeishuChannelConfig | None = None

    # ── 调试与日志配置 ──
    debug: bool = False
    log_token_usage: bool = True
    log_file: str | None = None

    # ── 高级配置 ──
    tool_selection_strategy: str = "toolbox"  # "all" | "toolbox" | "auto"
    loop_detection: dict[str, Any] = field(default_factory=dict)
    model_overrides: dict[str, Any] = field(default_factory=dict)
    risk_level: str | None = None  # "low" | "medium" | "high"
    history_progressive_compression: bool = True

    # ── 平铺字段（向后兼容，逐步弃用）──
    # 以下字段保留用于向后兼容，推荐使用 session_config 和 feishu_config 分组
    session_key: str | None = None
    session_workspace: str | None = None
    session_registry: ToolRegistryProtocol | None = None
    session_toolboxes: list[Toolbox] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    cli_loop_state: Any | None = None
    cli_dispatch_allow_mutations: bool = True
    feishu_receive_chat_id: str | None = None
    feishu_trigger_message_id: str | None = None
    feishu_root_id: str | None = None
    feishu_parent_id: str | None = None
    feishu_thread_id: str | None = None
    feishu_im_receive_id_type: str | None = None
    feishu_im_receive_id: str | None = None


def normalize_conversation_history(value: Any) -> list[dict[str, Any]]:
    """将 history.json 或调用方传入的值规范为 Chat API 消息 dict 列表。

    兼容：
    - 标准列表：[{"role":"user","content":"..."}, ...]
    - 包装对象：{"messages":[...], "session_id":...}（否则 ``*history`` 会展开 dict 的键名，
      得到 str，随后在 ``msg.get`` 处报错）
    """
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        value = value["messages"]
    if not isinstance(value, list):
        return []
    return [m for m in value if isinstance(m, dict) and isinstance(m.get("role"), str)]


__all__ = [
    "ModelConfig",
    "SessionBindingConfig",
    "FeishuChannelConfig",
    "AgentConfig",
    "normalize_conversation_history",
]
