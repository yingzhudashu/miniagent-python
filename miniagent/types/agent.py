"""Mini Agent Python — Agent 运行结果与统计类型

包含：

- ``AgentRunResult`` / ``AgentRunOptions``：单次 ``run_agent`` 或管线执行的输出与覆盖项
- ``ToolStats``、``ToolMonitorProtocol``：工具耗时与成功率统计（默认实现见
  ``miniagent.infrastructure.monitor``）
- ``LoopDetection*``：执行器内循环检测配置与结果（检测器见 ``loop_detector``）
- ``PipelineStep`` / ``PipelineResult``：无 LLM 循环的线性 ``run_pipeline`` 模式

**Protocol 最佳实践**：
- Protocol 不使用 @abstractmethod（Python Protocol 仅定义方法签名）
- 使用 @runtime_checkable 支持 isinstance() 检查
"""

from __future__ import annotations

import os as _os_for_agent
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# 配置默认值（支持环境变量覆盖）
_HISTORY_SIZE_DEFAULT = int(_os_for_agent.environ.get("MINIAGENT_HISTORY_SIZE", "50"))


@dataclass
class AgentRunResult:
    """Agent 运行结果

    Attributes:
        reply: 最终回复
        total_tool_calls: 工具调用总次数
        tool_stats: 各工具的详细统计
        used_tools: 本轮使用过的工具名称列表
    """

    reply: str = ""
    total_tool_calls: int = 0
    tool_stats: dict[str, ToolStats] = field(default_factory=dict)
    used_tools: list[str] = field(default_factory=list)


@dataclass
class AgentRunOptions:
    """Agent 运行选项

    Attributes:
        system_prompt: 系统提示词覆盖
        agent_config: Agent 层配置覆盖
        model_config: 模型层配置覆盖
    """

    system_prompt: str | None = None
    agent_config: dict[str, Any] | None = None
    model_config: dict[str, Any] | None = None


@dataclass
class ToolStats:
    """单个工具的调用统计

    Attributes:
        calls: 调用次数
        total_ms: 总耗时（毫秒）
        success_count: 成功次数
        fail_count: 失败次数
        errors: 错误信息列表
    """

    calls: int = 0
    total_ms: int = 0
    success_count: int = 0
    fail_count: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class LoopDetectionConfig:
    """循环检测配置

    Attributes:
        enabled: 是否启用循环检测
        history_size: 保留的最近工具调用历史条数（默认 50，可通过 MINIAGENT_HISTORY_SIZE 覆盖）
        warning_threshold: 警告阈值（默认 8）
        critical_threshold: 严重阈值（默认 12）
        detectors: 检测器开关
    """

    enabled: bool = True
    history_size: int = _HISTORY_SIZE_DEFAULT
    warning_threshold: int = 8
    critical_threshold: int = 12
    detectors: dict[str, bool] = field(
        default_factory=lambda: {
            "generic_repeat": True,
            "known_poll_no_progress": True,
            "ping_pong": True,
        }
    )


# 循环检测事件级别
LoopLevel = Literal["none", "warning", "critical"]


@dataclass
class LoopDetectionResult:
    """循环检测结果

    Attributes:
        level: 事件级别
        message: 消息说明
        pattern: 重复的工具调用模式
    """

    level: LoopLevel = "none"
    message: str = ""
    pattern: str | None = None


@dataclass
class PipelineStep:
    """管线中的单个步骤

    Attributes:
        tool: 要执行的工具名称
        args: 工具调用参数
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """管线执行结果

    Attributes:
        steps: 每个步骤的执行结果
        final_content: 最终累积内容
        success: 是否全部成功
    """

    steps: list[dict[str, Any]] = field(default_factory=list)
    final_content: str = ""
    success: bool = False


__all__ = [
    "AgentRunResult",
    "AgentRunOptions",
    "ToolStats",
    "ToolMonitorProtocol",
    "LoopDetectionConfig",
    "LoopLevel",
    "LoopDetectionResult",
    "PipelineStep",
    "PipelineResult",
]


# ============================================================================
# ToolMonitor Protocol
# ============================================================================


@runtime_checkable
class ToolMonitorProtocol(Protocol):
    """工具监控器接口协议

    记录工具调用统计，生成性能报告。

    该 Protocol 用于 ``miniagent.runtime.context.RuntimeContext`` 的
    monitor 字段类型，支持依赖注入模式。
    """

    def record(self, tool: str, duration_ms: int, success: bool) -> None:
        """记录一次工具调用

        Args:
            tool: 工具名称
            duration_ms: 耗时（毫秒）
            success: 是否成功
        """
        ...

    def get_stats(self, tool: str) -> ToolStats | None:
        """获取单个工具的统计

        Args:
            tool: 工具名称

        Returns:
            工具统计对象，若不存在则返回 None
        """
        ...

    def get_all_stats(self) -> dict[str, ToolStats]:
        """获取所有工具的统计

        Returns:
            工具名称到统计对象的映射
        """
        ...

    def report(self) -> str:
        """生成统计报告（可读文本）

        Returns:
            统计报告字符串
        """
        ...
