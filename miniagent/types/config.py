"""Mini Agent Python — 模型与 Agent 配置类型

双层配置体系：
- ModelConfig：模型层（API 端点、temperature、thinking 等）
- AgentConfig：Agent 层（max_turns、tool_timeout、上下文策略等）
- ModelProfile：模型配置预设（creative/balanced/precise 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Model Profile — 模型预设
# ============================================================================

# 内置预设名称
BuiltInProfile = str  # Literal["creative", "balanced", "precise", "code", "fast"]


@dataclass
class ModelProfile:
    """模型配置预设

    针对不同复杂度任务提供预调优的模型参数。

    Attributes:
        name: 预设名称
        temperature: 温度（创造性 vs 确定性）
        top_p: top_p 采样
        max_tokens: 最大输出 token 数
        thinking_level: thinking 级别
        thinking_budget: thinking token 预算
        description: 适用场景描述
    """

    name: str
    temperature: float
    top_p: float
    max_tokens: int
    thinking_level: str  # "disabled" | "light" | "medium" | "heavy"
    thinking_budget: int
    description: str


# ============================================================================
# ModelConfig — 模型层配置
# ============================================================================


@dataclass
class ModelConfig:
    """模型配置

    基础配置 + 预设覆盖 + 运行时覆盖

    Attributes:
        base_url: API 端点
        model: 模型名称
        temperature: 温度（0.0-2.0）
        top_p: top_p 采样（0.0-1.0）
        max_tokens: 最大输出 token 数
        thinking_level: thinking 级别
        thinking_budget: thinking token 预算
        context_window: 上下文窗口大小（token）
        stream: 是否使用流式输出
        retry_count: API 调用重试次数
        profiles: 模型配置预设字典
        active_profile: 当前使用的预设名称
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    thinking_level: str = "light"
    thinking_budget: int = 1024
    context_window: int = 128000
    stream: bool = False
    retry_count: int = 2
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    active_profile: str = "balanced"


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
        max_turns: 最大轮数（ReAct loop 迭代次数，默认 200）
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
        cli_dispatch_allow_mutations: capture 模式下是否允许 .session 等会改共享状态的子命令（飞书应为 False）
        feishu_receive_chat_id: 飞书 API 用 ``chat_id``（如 ``oc_xxx``）；注入工具上下文以便 ``run_dot_command`` 的 ``.abort`` 等作用于当前群队列
        history_progressive_compression: 是否启用磁盘会话历史的渐进式压缩（L1–L3）；关闭后仅归档/删轮
    """

    max_turns: int = 200
    tool_timeout: int = 60
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
    session_registry: Any | None = None  # ToolRegistryProtocol (optional session-level registry)
    session_toolboxes: list[Any] = field(default_factory=list)  # list[Toolbox]
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    risk_level: str | None = None  # "low" | "medium" | "high"
    cli_loop_state: Any | None = None
    cli_dispatch_allow_mutations: bool = True
    feishu_receive_chat_id: str | None = None
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
    "BuiltInProfile",
    "ModelProfile",
    "ModelConfig",
    "AgentConfig",
    "normalize_conversation_history",
]
