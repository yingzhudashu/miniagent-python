"""Mini Agent Python — 模型与 Agent 配置类型

双层配置体系：
- ModelConfig：模型层（API 端点、temperature、thinking 等）
- AgentConfig：Agent 层（max_turns、tool_timeout、上下文策略等）
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
        thinking_level: thinking 级别
        thinking_budget: thinking token 预算
        context_window: 上下文窗口大小（token）
        retry_count: API 调用重试次数
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
# AgentConfig — Agent 层配置
# ============================================================================


@dataclass
class AgentConfig:
    """Agent 配置

    配置合并优先级（从低到高）：
    1. get_default_agent_config() — 默认值
    2. run_agent(options.agent_config) — 用户传入
    3. plan.suggested_config — 规划器推荐

    Attributes:
        max_turns: 最大轮数（ReAct loop 迭代次数；运行时由 get_default_agent_config() 从 JSON 加载，默认 400）
        tool_timeout: 工具超时（秒，默认 60）
        http_timeout: HTTP 超时（秒，默认 120）
        context_reserve_ratio: 上下文保留比例（默认 0.15）
        context_compress_threshold: 上下文触发压缩的阈值比例（默认 0.6）
        context_overflow_strategy: 上下文溢出处理策略
        compress_messages: 是否压缩消息
        tool_selection_strategy: 工具选择策略
        auto_execute_confirmed: 是否自动确认执行
        allow_parallel_tools: 是否允许并行工具调用
        response_language: 响应语言（默认 zh-CN）
        response_format: 响应格式
        debug: 调试模式
        log_token_usage: 是否记录 token 用量
        log_file: 增量日志文件路径
        loop_detection: 循环检测配置
        model_overrides: 模型覆盖
        session_key: 会话记忆键
        session_workspace: 会话工作空间路径
        session_registry: 会话级工具注册表（可选）
        session_toolboxes: 会话工具箱列表
        conversation_history: 对话历史（跨轮次保留）
        risk_level: 风险等级（来自规划建议或计划，供执行阶段提示）
        cli_loop_state: 与 CLI/飞书共享的 CliLoopState dict（供 run_dot_command 等工具调用 dispatch_command）
        cli_dispatch_allow_mutations: capture 模式下是否允许 .session 等会改共享状态的子命令（飞书默认 False；MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时为 True）
        feishu_receive_chat_id: 飞书 API 用 ``chat_id``（如 ``oc_xxx``）；注入工具上下文以便 ``run_dot_command`` 的 ``.abort`` 等作用于当前群队列
        feishu_trigger_message_id: 触发本轮的飞书入站 ``message_id``（可选；供工具/提示词与 ``MINIAGENT_FEISHU_REPLY_TARGET=reply`` 出站）
        feishu_root_id: 入站事件 ``root_id``（话题根消息，可选；与历史方案中的「reply_root」语境一致）
        feishu_parent_id: 入站事件 ``parent_id``（可选）
        feishu_thread_id: 入站事件 ``thread_id``（可选；话题上下文）
        feishu_im_receive_id_type: 飞书 IM ``create`` 消息的 ``receive_id_type``（``chat_id`` / ``open_id`` / ``union_id``）；缺省由工具上下文读环境变量 ``MINIAGENT_FEISHU_RECEIVE_ID_TYPE``
        feishu_im_receive_id: 与上一项配合：非 ``chat_id`` 时作为默认 ``receive_id``（通常为入站发送者 ``open_id``）
        history_progressive_compression: 是否启用磁盘会话历史的渐进式压缩（L1–L3）；关闭后仅归档/删轮
    """

    max_turns: int = _MAX_TURNS_DEFAULT
    tool_timeout: int = _TOOL_TIMEOUT_DEFAULT
    http_timeout: int = 120
    context_reserve_ratio: float = 0.15
    context_compress_threshold: float = 0.6
    context_overflow_strategy: str = "summarize"  # "summarize" | "truncate" | "error"
    compress_messages: bool = True
    tool_selection_strategy: str = "toolbox"  # "all" | "toolbox" | "auto"
    auto_execute_confirmed: bool = False
    allow_parallel_tools: bool = True
    response_language: str = "zh-CN"
    response_format: str = "markdown"  # "text" | "markdown" | "structured"
    debug: bool = False
    log_token_usage: bool = True
    log_file: str | None = None
    loop_detection: dict[str, Any] = field(default_factory=dict)
    model_overrides: dict[str, Any] = field(default_factory=dict)
    session_key: str | None = None
    session_workspace: str | None = None
    session_registry: ToolRegistryProtocol | None = None
    session_toolboxes: list[Toolbox] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    risk_level: str | None = None  # "low" | "medium" | "high"
    cli_loop_state: Any | None = None
    cli_dispatch_allow_mutations: bool = True
    feishu_receive_chat_id: str | None = None
    feishu_trigger_message_id: str | None = None
    feishu_root_id: str | None = None
    feishu_parent_id: str | None = None
    feishu_thread_id: str | None = None
    feishu_im_receive_id_type: str | None = None
    feishu_im_receive_id: str | None = None
    history_progressive_compression: bool = True


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
    "AgentConfig",
    "normalize_conversation_history",
]
