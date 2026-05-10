"""Mini Agent Python — 规划相关类型

两阶段架构的 Phase 1（Planning）类型定义：
- StructuredPlan: 结构化执行计划
- PlanStep: 计划步骤
- PlanChunk: 分块执行单元
- SuggestedConfig: 推荐配置
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanStep:
    """计划中的单个步骤

    Attributes:
        step_number: 步骤序号
        description: 步骤描述
        required_toolboxes: 需要的工具箱 ID 列表
        expected_input: 期望输入
        expected_output: 期望输出
        depends_on: 依赖的步骤序号（None 表示无依赖）
    """

    step_number: int
    description: str
    required_toolboxes: list[str] = field(default_factory=list)
    expected_input: str = ""
    expected_output: str = ""
    depends_on: int | None = None


@dataclass
class PlanChunk:
    """计划分块：当任务过大时，将计划拆分为多个 chunk

    Attributes:
        chunk_number: 分块序号
        steps: 该分块包含的步骤
        estimated_tokens: 预估 token 消耗
        chunk_system_prompt: 该分块的 system prompt 增强
    """

    chunk_number: int
    steps: list[PlanStep] = field(default_factory=list)
    estimated_tokens: int = 0
    chunk_system_prompt: str = ""


@dataclass
class SuggestedConfig:
    """规划器推荐的运行时配置

    在 Phase 1 生成，Phase 2 执行时与默认配置和用户配置合并。

    Attributes:
        max_turns: 推荐的最大轮数
        tool_timeout: 推荐的工具超时
        context_overflow_strategy: 推荐的上下文溢出策略
        tool_selection_strategy: 推荐的工具选择策略
        model_overrides: 推荐的模型覆盖
        thinking_level: 推荐的 thinking 级别
        chunk_execution: 是否启用分块执行
        chunk_token_budget: 分块执行的 token 预算
        parallelism: 并行策略
        risk_level: 风险等级
    """

    max_turns: int | None = None
    tool_timeout: int | None = None
    context_overflow_strategy: str | None = None
    tool_selection_strategy: str | None = None
    model_overrides: dict | None = None
    thinking_level: str | None = None
    chunk_execution: bool = False
    chunk_token_budget: int | None = None
    parallelism: str | None = None  # "sequential" | "safe-parallel" | "full-parallel"
    risk_level: str | None = None  # "low" | "medium" | "high"


@dataclass
class EstimatedCost:
    """成本预估"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_usd: float = 0.0


@dataclass
class OutputSpec:
    """输出规格"""

    language: str = "zh-CN"
    format: str = "markdown"  # "text" | "markdown" | "structured"
    expected_deliverable: str = ""


@dataclass
class FallbackPlan:
    """回退计划"""

    degrade_to_simple: bool = False
    degraded_max_turns: int = 10


@dataclass
class ContextStrategy:
    """上下文策略"""

    mode: str = "normal"  # "normal" | "chunked" | "summarize" | "truncate"
    chunks: list[PlanChunk] | None = None
    reason: str = ""


@dataclass
class EstimatedTokens:
    """Token 消耗预估"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_result_tokens: int = 0
    total: int = 0


@dataclass
class StructuredPlan:
    """结构化执行计划（Phase 1 的产物）

    由 LLM 根据用户需求和可用工具箱生成，包含：
    - 步骤分解
    - 工具箱选择
    - 配置推荐
    - Token 预估
    - 风险等级
    - 输出规格

    Attributes:
        summary: 计划摘要
        steps: 执行步骤列表
        required_toolboxes: 需要的工具箱 ID 列表
        suggested_config: 推荐的运行时配置
        estimated_tokens: Token 消耗预估
        context_strategy: 上下文策略
        requires_confirmation: 是否需要用户确认
        confirmation_message: 确认消息
        risk_level: 风险等级
        estimated_cost: 成本预估
        output_spec: 输出规格
        fallback_plan: 回退计划
    """

    summary: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    required_toolboxes: list[str] = field(default_factory=list)
    suggested_config: SuggestedConfig = field(default_factory=SuggestedConfig)
    estimated_tokens: EstimatedTokens = field(default_factory=EstimatedTokens)
    context_strategy: ContextStrategy = field(default_factory=ContextStrategy)
    requires_confirmation: bool = False
    confirmation_message: str | None = None
    risk_level: str = "low"  # "low" | "medium" | "high"
    estimated_cost: EstimatedCost = field(default_factory=EstimatedCost)
    output_spec: OutputSpec = field(default_factory=OutputSpec)
    fallback_plan: FallbackPlan = field(default_factory=FallbackPlan)


__all__ = [
    "PlanStep",
    "PlanChunk",
    "SuggestedConfig",
    "StructuredPlan",
]
