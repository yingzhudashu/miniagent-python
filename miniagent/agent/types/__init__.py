"""Mini Agent Python — 无 I/O 的领域数据模型。

本包只放 **数据结构契约**，不包含 I/O 或业务编排。实现落在 ``miniagent.agent``、
``miniagent.assistant.memory``、``miniagent.assistant.infrastructure`` 等模块。

架构总览见 ``docs/ARCHITECTURE.md``；记忆语义见 ``docs/MEMORY_SYSTEM.md``。

模块划分：

- ``tool``: 工具定义、``ToolContext``、注册表协议、上下文压缩相关类型
- ``config``: ``ModelConfig`` / ``AgentConfig`` 等双层配置
- ``memory``: 记忆与会话的 **类型**；持久化实现见 ``miniagent.assistant.memory``
- ``memory_context``: 记忆上下文 Protocol（注入/检索/历史）
- ``skill``: 技能包、ClawHub 协议
- ``agent``: 运行结果、监控协议、循环检测配置、线性管线
- ``confirmation``: 澄清/计划确认请求与结果（``ConfirmationRequest`` / ``ConfirmationResult``）
- ``planning``: Phase 1 结构化计划（``StructuredPlan`` 等）
- 应用运行时端口统一位于 ``miniagent.assistant.contracts``，不从本包反向聚合导出
- ``error_prefix`` / ``error_messages``: 工具与 CLI 的统一输出前缀与用户可见消息常量
- ``errors``: 项目自定义异常类型（沙箱、飞书配置/依赖等）
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "AgentConfig": "miniagent.agent.types.config",
    "AgentRunOptions": "miniagent.agent.types.agent",
    "AgentRunResult": "miniagent.agent.types.agent",
    "ClawHubClientProtocol": "miniagent.agent.types.skill",
    "ClawHubSearchResult": "miniagent.agent.types.skill",
    "ClawHubSkillDetail": "miniagent.agent.types.skill",
    "ConfirmationRequest": "miniagent.agent.types.confirmation",
    "ConfirmationResult": "miniagent.agent.types.confirmation",
    "ConfirmationStage": "miniagent.agent.types.confirmation",
    "ContextManagerProtocol": "miniagent.agent.types.tool",
    "ContextState": "miniagent.agent.types.tool",
    "ContextStrategy": "miniagent.agent.types.planning",
    "ERROR_PREFIX": "miniagent.agent.types.error_prefix",
    "EstimatedCost": "miniagent.agent.types.planning",
    "EstimatedTokens": "miniagent.agent.types.planning",
    "FallbackPlan": "miniagent.agent.types.planning",
    "FeishuConfigMissingError": "miniagent.agent.types.errors",
    "FileMetadata": "miniagent.agent.types.memory",
    "GroundTruthFact": "miniagent.agent.types.memory",
    "LarkOapiMissingError": "miniagent.agent.types.errors",
    "LoopDetectionConfig": "miniagent.agent.types.agent",
    "LoopDetectionResult": "miniagent.agent.types.agent",
    "LoopLevel": "miniagent.agent.types.agent",
    "MemoryContextProtocol": "miniagent.agent.types.memory_context",
    "MemoryEntry": "miniagent.agent.types.memory",
    "MemoryEntryInput": "miniagent.agent.types.memory",
    "MemoryHistoryProtocol": "miniagent.agent.types.memory_context",
    "MemoryInjectionResult": "miniagent.agent.types.memory_context",
    "MemorySearchProtocol": "miniagent.agent.types.memory_context",
    "MemoryStoreProtocol": "miniagent.agent.types.memory",
    "ModelConfig": "miniagent.agent.types.config",
    "OutputSpec": "miniagent.agent.types.planning",
    "PipelineResult": "miniagent.agent.types.agent",
    "PipelineStep": "miniagent.agent.types.agent",
    "PipelineStepRecord": "miniagent.agent.types.agent",
    "PlanChunk": "miniagent.agent.types.planning",
    "PlanConfirmationAction": "miniagent.agent.types.confirmation",
    "PlanStep": "miniagent.agent.types.planning",
    "RegisteredTool": "miniagent.agent.types.tool",
    "SUCCESS_PREFIX": "miniagent.agent.types.error_prefix",
    "SandboxViolationError": "miniagent.agent.types.errors",
    "Session": "miniagent.agent.types.memory",
    "SessionManagerProtocol": "miniagent.agent.types.memory",
    "SessionMemory": "miniagent.agent.types.memory",
    "SessionOptions": "miniagent.agent.types.memory",
    "Skill": "miniagent.agent.types.skill",
    "SkillEntry": "miniagent.agent.types.skill",
    "SkillMetadata": "miniagent.agent.types.skill",
    "SkillPackage": "miniagent.agent.types.skill",
    "SkillRegistryProtocol": "miniagent.agent.types.skill",
    "StructuredPlan": "miniagent.agent.types.planning",
    "SuggestedConfig": "miniagent.agent.types.planning",
    "TokenEstimate": "miniagent.agent.types.tool",
    "ToolCallResult": "miniagent.agent.types.agent",
    "ToolContext": "miniagent.agent.types.tool",
    "ToolDefinition": "miniagent.agent.types.tool",
    "ToolHandler": "miniagent.agent.types.tool",
    "ToolMonitorProtocol": "miniagent.agent.types.agent",
    "ToolPermission": "miniagent.agent.types.tool",
    "ToolRegistryProtocol": "miniagent.agent.types.tool",
    "ToolResult": "miniagent.agent.types.tool",
    "ToolRuntimePermission": "miniagent.agent.types.tool",
    "ToolStats": "miniagent.agent.types.agent",
    "Toolbox": "miniagent.agent.types.tool",
    "WARNING_PREFIX": "miniagent.agent.types.error_prefix",
    "WireAPI": "miniagent.agent.types.config",
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
