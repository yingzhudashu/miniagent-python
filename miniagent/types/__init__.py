"""Mini Agent Python — 领域类型与协议（Pydantic/dataclass/Protocol）

本包只放 **数据结构契约**，不包含 I/O 或业务编排。实现落在 ``miniagent.core``、
``miniagent.memory``、``miniagent.infrastructure`` 等模块。

模块划分：

- ``tool``: 工具定义、``ToolContext``、注册表协议、上下文压缩相关类型
- ``config``: ``ModelProfile`` / ``AgentConfig`` 等双层配置
- ``memory``: 记忆与会话的 **类型**；持久化实现见 ``miniagent.memory``
- ``skill``: 技能包、ClawHub 协议
- ``agent``: 运行结果、监控协议、循环检测配置、线性管线
- ``planning``: Phase 1 结构化计划（``StructuredPlan`` 等）
- ``feishu``: 自 ``miniagent.feishu.types`` 再导出，便于 ``from miniagent.types import …``
"""

from miniagent.types.tool import (
    ToolPermission,
    Toolbox,
    ToolContext,
    ToolResult,
    ToolDefinition,
    RegisteredTool,
    ToolRegistryProtocol,
    TokenEstimate,
    ContextState,
    ContextManagerProtocol,
)
from miniagent.types.config import (
    ModelProfile,
    BuiltInProfile,
    ModelConfig,
    AgentConfig,
)
from miniagent.types.memory import (
    MemoryEntry,
    MemoryEntryInput,
    SessionMemory,
    MemoryStoreProtocol,
    SessionOptions,
    Session,
    SessionManagerProtocol,
)
from miniagent.types.skill import (
    SkillMetadata,
    SkillEntry,
    Skill,
    SkillPackage,
    SkillRegistryProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    ClawHubClientProtocol,
)
from miniagent.types.agent import (
    AgentRunResult,
    AgentRunOptions,
    ToolStats,
    ToolMonitorProtocol,
    LoopDetectionConfig,
    LoopLevel,
    LoopDetectionResult,
    PipelineStep,
    PipelineResult,
)
from miniagent.types.planning import (
    PlanStep,
    PlanChunk,
    SuggestedConfig,
    StructuredPlan,
)
from miniagent.feishu.types import FeishuConfig, FeishuMessageEvent, FeishuReply

__all__ = [
    # tool
    "ToolPermission",
    "Toolbox",
    "ToolContext",
    "ToolResult",
    "ToolDefinition",
    "RegisteredTool",
    "ToolRegistryProtocol",
    "TokenEstimate",
    "ContextState",
    "ContextManagerProtocol",
    # config
    "ModelProfile",
    "BuiltInProfile",
    "ModelConfig",
    "AgentConfig",
    # memory
    "MemoryEntry",
    "MemoryEntryInput",
    "SessionMemory",
    "MemoryStoreProtocol",
    "SessionOptions",
    "Session",
    "SessionManagerProtocol",
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
    "PipelineResult",
    # planning
    "PlanStep",
    "PlanChunk",
    "SuggestedConfig",
    "StructuredPlan",
    # feishu
    "FeishuMessageEvent",
    "FeishuConfig",
    "FeishuReply",
]
