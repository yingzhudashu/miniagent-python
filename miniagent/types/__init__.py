"""Mini Agent Python — 领域类型与协议（Pydantic/dataclass/Protocol）

本包只放 **数据结构契约**，不包含 I/O 或业务编排。实现落在 ``miniagent.core``、
``miniagent.memory``、``miniagent.infrastructure`` 等模块。

架构总览见 ``docs/ARCHITECTURE.md``；记忆语义见 ``docs/MEMORY_SYSTEM.md``。

模块划分：

- ``tool``: 工具定义、``ToolContext``、注册表协议、上下文压缩相关类型
- ``config``: ``ModelConfig`` / ``AgentConfig`` 等双层配置
- ``memory``: 记忆与会话的 **类型**；持久化实现见 ``miniagent.memory``
- ``memory_context``: 记忆上下文 Protocol（注入/检索/历史）
- ``skill``: 技能包、ClawHub 协议
- ``agent``: 运行结果、监控协议、循环检测配置、线性管线
- ``confirmation``: 澄清/计划确认请求与结果（``ConfirmationRequest`` / ``ConfirmationResult``）
- ``planning``: Phase 1 结构化计划（``StructuredPlan`` 等）
- ``protocols``: 运行时注入协议（ActivityLogProtocol、KeywordIndexProtocol）
- ``error_prefix`` / ``error_messages``: 工具与 CLI 的统一输出前缀与用户可见消息常量
- ``errors``: 项目自定义异常类型（沙箱、飞书配置/依赖等）
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "ActivityLogProtocol": "miniagent.types.protocols",
    "AgentConfig": "miniagent.types.config",
    "AgentRunOptions": "miniagent.types.agent",
    "AgentRunResult": "miniagent.types.agent",
    "ChannelRouterProtocol": "miniagent.types.protocols",
    "ClawHubClientProtocol": "miniagent.types.skill",
    "ClawHubSearchResult": "miniagent.types.skill",
    "ClawHubSkillDetail": "miniagent.types.skill",
    "ConfirmationRequest": "miniagent.types.confirmation",
    "ConfirmationResult": "miniagent.types.confirmation",
    "ConfirmationStage": "miniagent.types.confirmation",
    "ContextManagerProtocol": "miniagent.types.tool",
    "ContextState": "miniagent.types.tool",
    "ContextStrategy": "miniagent.types.planning",
    "ERROR_PREFIX": "miniagent.types.error_prefix",
    "EstimatedCost": "miniagent.types.planning",
    "EstimatedTokens": "miniagent.types.planning",
    "FallbackPlan": "miniagent.types.planning",
    "FeishuConfigMissingError": "miniagent.types.errors",
    "FeishuRuntimeProtocol": "miniagent.types.protocols",
    "FileMetadata": "miniagent.types.memory",
    "GroundTruthFact": "miniagent.types.memory",
    "KeywordIndexProtocol": "miniagent.types.protocols",
    "LarkOapiMissingError": "miniagent.types.errors",
    "LoopDetectionConfig": "miniagent.types.agent",
    "LoopDetectionResult": "miniagent.types.agent",
    "LoopLevel": "miniagent.types.agent",
    "MemoryContextProtocol": "miniagent.types.memory_context",
    "MemoryEntry": "miniagent.types.memory",
    "MemoryEntryInput": "miniagent.types.memory",
    "MemoryHistoryProtocol": "miniagent.types.memory_context",
    "MemoryInjectionResult": "miniagent.types.memory_context",
    "MemorySearchProtocol": "miniagent.types.memory_context",
    "MemoryStoreProtocol": "miniagent.types.memory",
    "MessageQueueProtocol": "miniagent.types.protocols",
    "ModelConfig": "miniagent.types.config",
    "OnPlan": "miniagent.types.protocols",
    "OnThinking": "miniagent.types.protocols",
    "OnThinkingCallback": "miniagent.types.protocols",
    "OnToolCall": "miniagent.types.protocols",
    "OnToolFinish": "miniagent.types.protocols",
    "OnToolFinishCallback": "miniagent.types.protocols",
    "OutputSpec": "miniagent.types.planning",
    "PipelineResult": "miniagent.types.agent",
    "PipelineStep": "miniagent.types.agent",
    "PipelineStepRecord": "miniagent.types.agent",
    "PlanChunk": "miniagent.types.planning",
    "PlanConfirmationAction": "miniagent.types.confirmation",
    "PlanStep": "miniagent.types.planning",
    "RegisteredTool": "miniagent.types.tool",
    "SUCCESS_PREFIX": "miniagent.types.error_prefix",
    "SandboxViolationError": "miniagent.types.errors",
    "Session": "miniagent.types.memory",
    "SessionManagerProtocol": "miniagent.types.memory",
    "SessionMemory": "miniagent.types.memory",
    "SessionOptions": "miniagent.types.memory",
    "Skill": "miniagent.types.skill",
    "SkillEntry": "miniagent.types.skill",
    "SkillMetadata": "miniagent.types.skill",
    "SkillPackage": "miniagent.types.skill",
    "SkillRegistryProtocol": "miniagent.types.skill",
    "StructuredPlan": "miniagent.types.planning",
    "SuggestedConfig": "miniagent.types.planning",
    "TokenEstimate": "miniagent.types.tool",
    "ToolCallResult": "miniagent.types.agent",
    "ToolContext": "miniagent.types.tool",
    "ToolDefinition": "miniagent.types.tool",
    "ToolHandler": "miniagent.types.tool",
    "ToolMonitorProtocol": "miniagent.types.agent",
    "ToolPermission": "miniagent.types.tool",
    "ToolRegistryProtocol": "miniagent.types.tool",
    "ToolResult": "miniagent.types.tool",
    "ToolRuntimePermission": "miniagent.types.tool",
    "ToolStats": "miniagent.types.agent",
    "Toolbox": "miniagent.types.tool",
    "UnifiedEngineProtocol": "miniagent.types.protocols",
    "WARNING_PREFIX": "miniagent.types.error_prefix",
    "WireAPI": "miniagent.types.config",
}


def __getattr__(name: str) -> Any:
    """Load aggregate type exports only when explicitly requested."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy aggregate names to discovery and documentation tools."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    # tool
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
    # config
    "ModelConfig",
    "WireAPI",
    "AgentConfig",
    # memory
    "FileMetadata",
    "GroundTruthFact",
    "MemoryEntry",
    "MemoryEntryInput",
    "SessionMemory",
    "MemoryStoreProtocol",
    "SessionOptions",
    "Session",
    "SessionManagerProtocol",
    # memory_context
    "MemoryInjectionResult",
    "MemoryContextProtocol",
    "MemorySearchProtocol",
    "MemoryHistoryProtocol",
    # protocols (运行时注入)
    "ActivityLogProtocol",
    "KeywordIndexProtocol",
    "UnifiedEngineProtocol",
    "ChannelRouterProtocol",
    "MessageQueueProtocol",
    "FeishuRuntimeProtocol",
    "OnThinkingCallback",
    "OnToolFinishCallback",
    "OnToolCall",
    "OnPlan",
    "OnThinking",
    "OnToolFinish",
    # skill
    "SkillMetadata",
    "SkillEntry",
    "Skill",
    "SkillPackage",
    "SkillRegistryProtocol",
    "ClawHubSearchResult",
    "ClawHubSkillDetail",
    "ClawHubClientProtocol",
    # agent
    "AgentRunResult",
    "AgentRunOptions",
    "ToolStats",
    "ToolMonitorProtocol",
    "LoopDetectionConfig",
    "LoopLevel",
    "LoopDetectionResult",
    "PipelineStep",
    "PipelineStepRecord",
    "PipelineResult",
    "ToolCallResult",
    # confirmation
    "ConfirmationStage",
    "ConfirmationRequest",
    "ConfirmationResult",
    "PlanConfirmationAction",
    # planning
    "PlanStep",
    "PlanChunk",
    "SuggestedConfig",
    "StructuredPlan",
    "ContextStrategy",
    "EstimatedTokens",
    "EstimatedCost",
    "OutputSpec",
    "FallbackPlan",
    # output prefixes
    "ERROR_PREFIX",
    "WARNING_PREFIX",
    "SUCCESS_PREFIX",
    # errors
    "SandboxViolationError",
    "FeishuConfigMissingError",
    "LarkOapiMissingError",
]
