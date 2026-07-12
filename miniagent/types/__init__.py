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

from miniagent.types.agent import (
    AgentRunOptions,
    AgentRunResult,
    LoopDetectionConfig,
    LoopDetectionResult,
    LoopLevel,
    PipelineResult,
    PipelineStep,
    PipelineStepRecord,
    ToolCallResult,
    ToolMonitorProtocol,
    ToolStats,
)
from miniagent.types.config import (
    AgentConfig,
    ModelConfig,
)
from miniagent.types.confirmation import (
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationStage,
    PlanConfirmationAction,
)
from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX
from miniagent.types.errors import (
    FeishuConfigMissingError,
    LarkOapiMissingError,
    SandboxViolationError,
)
from miniagent.types.memory import (
    FileMetadata,
    GroundTruthFact,
    MemoryEntry,
    MemoryEntryInput,
    MemoryStoreProtocol,
    Session,
    SessionManagerProtocol,
    SessionMemory,
    SessionOptions,
)
from miniagent.types.memory_context import (
    MemoryContextProtocol,
    MemoryHistoryProtocol,
    MemoryInjectionResult,
    MemorySearchProtocol,
)
from miniagent.types.planning import (
    ContextStrategy,
    EstimatedCost,
    EstimatedTokens,
    FallbackPlan,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
    SuggestedConfig,
)
from miniagent.types.protocols import (
    ActivityLogProtocol,
    ChannelRouterProtocol,
    FeishuRuntimeProtocol,
    KeywordIndexProtocol,
    MessageQueueProtocol,
    OnPlan,
    OnThinking,
    OnThinkingCallback,
    OnToolCall,
    OnToolFinish,
    OnToolFinishCallback,
    UnifiedEngineProtocol,
)
from miniagent.types.skill import (
    ClawHubClientProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    Skill,
    SkillEntry,
    SkillMetadata,
    SkillPackage,
    SkillRegistryProtocol,
)
from miniagent.types.tool import (
    ContextManagerProtocol,
    ContextState,
    RegisteredTool,
    TokenEstimate,
    Toolbox,
    ToolContext,
    ToolDefinition,
    ToolHandler,
    ToolPermission,
    ToolRegistryProtocol,
    ToolResult,
    ToolRuntimePermission,
)

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
