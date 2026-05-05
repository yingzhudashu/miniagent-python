"""Mini Agent Python — 类型定义模块

领域划分（6 个文件）：
- tool: 工具/工具箱/注册表 + 上下文管理
- config: 双层配置体系
- memory: 记忆存储 + 会话管理
- skill: 技能系统 + ClawHub 技能市场
- agent: Agent 运行结果 + 统计 + 循环检测 + 管线
- planning: 规划系统
- feishu: 飞书集成
"""

from src.types.tool import (
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
from src.types.config import (
    ModelProfile,
    BuiltInProfile,
    ModelConfig,
    AgentConfig,
)
from src.types.memory import (
    MemoryEntry,
    MemoryEntryInput,
    SessionMemory,
    MemoryStoreProtocol,
    SessionOptions,
    Session,
    SessionManagerProtocol,
)
from src.types.skill import (
    SkillMetadata,
    SkillEntry,
    Skill,
    SkillPackage,
    SkillRegistryProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    ClawHubClientProtocol,
)
from src.types.agent import (
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
from src.types.planning import (
    PlanStep,
    PlanChunk,
    SuggestedConfig,
    StructuredPlan,
)
from src.types.feishu import (
    FeishuMessageEvent,
    FeishuConfig,
    FeishuMessagePayload,
    AgentMessageResult,
)

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
    "FeishuMessagePayload",
    "AgentMessageResult",
]
