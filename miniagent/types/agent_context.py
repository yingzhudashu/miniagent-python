"""Agent执行上下文数据类。

使用dataclass合并run_agent的18个参数为清晰的配置对象。

设计原则：
- Clean Code：参数≤10（推荐3-4）
- 类型安全：所有字段有明确类型
- 默认值合理：所有可选字段有默认值
- 可验证：to_agent_config()进行验证

详见 docs/ARCHITECTURE.md（Agent上下文）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from miniagent.types.agent import ToolMonitorProtocol
from miniagent.types.protocols import (
    OnPlan,
    OnThinking,
    OnToolCall,
    OnToolFinish,
    ToolRegistryProtocol,
)


@dataclass
class AgentContext:
    """Agent执行上下文（参数合并）。

    将run_agent的18个参数合并为5个主要配置组：
    - user_input: 用户输入（必需）
    - tool_config: 工具和监控配置
    - agent_config: Agent配置
    - callback_config: 回调函数配置
    - runtime_config: 运行时环境配置

    Example:
        context = AgentContext(
            user_input="读取README.md文件",
            tool_config=ToolConfig(
                registry=registry,
                toolboxes=[toolbox],
            ),
            session_key="test-session",
        )
        result = await run_agent(context)
    """

    # ── 必需参数 ──
    user_input: str  # 用户原始需求

    # ── 工具配置 ──
    registry: ToolRegistryProtocol | None = None  # 工具注册表（可选，可从toolboxes推导）
    monitor: ToolMonitorProtocol | None = None  # 性能监控器（可选）
    toolboxes: list[Any] | None = None  # 可用工具箱列表（可选）

    # ── Agent配置 ──
    agent_config: dict[str, Any] | None = None  # Agent配置覆盖（可选）
    system_prompt: str | None = None  # 自定义系统提示词（可选）
    skip_planning: bool = False  # 跳过规划阶段（默认False）

    # ── 回调配置 ──
    on_tool_call: OnToolCall | None = None  # 工具调用回调（可选）
    on_tool_finish: OnToolFinish | None = None  # 工具完成回调（可选）
    on_plan: OnPlan | None = None  # 计划确认回调（可选）
    on_thinking: OnThinking | None = None  # 思考过程回调（可选）

    # ── 运行时配置 ──
    session_key: str | None = None  # 会话标识符（可选）
    clawhub: Any | None = None  # ClawHub客户端（可选）
    memory_store: Any | None = None  # 记忆存储（可选）
    activity_log: Any | None = None  # 活动日志（可选）
    keyword_index: Any | None = None  # 关键词索引（可选）
    client: AsyncOpenAI | None = None  # LLM客户端（可选）
    clarifier: Any | None = None  # 需求澄清器（可选）
    confirmation_channel: Any | None = None  # 确认通道（可选）
    engine: Any | None = None  # 引擎实例（可选）

    def to_kwargs(self) -> dict[str, Any]:
        """转换为run_agent兼容的kwargs字典。

        Returns:
            kwargs字典（可直接传给run_agent）

        Note:
            提取所有非None字段，保持向后兼容。
        """
        kwargs = {
            "user_input": self.user_input,
        }

        # 添加可选参数（仅添加非None值）
        optional_fields = [
            "registry",
            "monitor",
            "toolboxes",
            "agent_config",
            "system_prompt",
            "skip_planning",
            "on_tool_call",
            "on_tool_finish",
            "on_plan",
            "on_thinking",
            "session_key",
            "clawhub",
            "memory_store",
            "activity_log",
            "keyword_index",
            "client",
            "clarifier",
            "confirmation_channel",
            "engine",
        ]

        for field_name in optional_fields:
            value = getattr(self, field_name, None)
            if value is not None:
                kwargs[field_name] = value

        return kwargs


@dataclass
class ToolConfig:
    """工具配置（简化版）。

    合并registry、monitor、toolboxes为单一对象。

    Example:
        tool_config = ToolConfig(
            registry=get_global_tool_registry(),
            toolboxes=[toolbox],
        )
    """

    registry: ToolRegistryProtocol
    monitor: ToolMonitorProtocol | None = None
    toolboxes: list[Any] | None = None


@dataclass
class CallbackConfig:
    """回调配置（简化版）。

    合并所有回调函数为单一对象。

    Example:
        callback_config = CallbackConfig(
            on_thinking=my_thinking_callback,
            on_tool_finish=my_tool_finish_callback,
        )
    """

    on_tool_call: OnToolCall | None = None
    on_tool_finish: OnToolFinish | None = None
    on_plan: OnPlan | None = None
    on_thinking: OnThinking | None = None


@dataclass
class RuntimeConfig:
    """运行时配置（简化版）。

    合并所有运行时环境参数为单一对象。

    Example:
        runtime_config = RuntimeConfig(
            session_key="test-session",
            memory_store=memory_store,
        )
    """

    session_key: str | None = None
    clawhub: Any | None = None
    memory_store: Any | None = None
    activity_log: Any | None = None
    keyword_index: Any | None = None
    client: AsyncOpenAI | None = None
    clarifier: Any | None = None
    confirmation_channel: Any | None = None
    engine: Any | None = None


__all__ = [
    "AgentContext",
    "ToolConfig",
    "CallbackConfig",
    "RuntimeConfig",
]