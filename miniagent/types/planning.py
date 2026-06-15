"""Mini Agent Python — 规划相关类型

两阶段架构的 Phase 1（Planning）类型定义。本模块仅含数据契约，解析与运行时逻辑见
:mod:`miniagent.core.planner`、:mod:`miniagent.core.plan_utils`、:mod:`miniagent.core.agent`、
:mod:`miniagent.core.executor`。

类型一览：
- :class:`PlanStep` — 计划步骤（Phase 2 分步执行）
- :class:`PlanChunk` — 分块执行单元（大任务拆分）
- :class:`SuggestedConfig` — 推荐运行时配置（与 AgentConfig 合并）
- :class:`ContextStrategy` — 上下文处理策略
- :class:`EstimatedTokens` / :class:`EstimatedCost` — 消耗与成本预估
- :class:`OutputSpec` — 输出交付规格
- :class:`FallbackPlan` — 执行失败时的降级策略
- :class:`StructuredPlan` — Phase 1 完整产物
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ThinkingLevel = Literal["low", "medium", "high"]
RiskLevel = Literal["low", "medium", "high"]
Parallelism = Literal["sequential", "safe-parallel", "full-parallel"]
ContextMode = Literal["normal", "chunked", "summarize", "truncate"]
OutputFormat = Literal["text", "markdown", "structured"]


@dataclass
class PlanStep:
    """计划中的单个步骤。

    Phase 2 在 ``PHASED_EXECUTION`` 开启且 ``StructuredPlan.steps`` 非空时逐步执行。
    ``depends_on`` 由 :func:`miniagent.core.plan_utils.order_steps_by_dependencies` 拓扑排序。

    Attributes:
        step_number: 步骤序号（规划器规范化后从 1 起连续编号）
        description: 步骤描述
        required_toolboxes: 本步需要的工具箱 ID 列表
        expected_input: 期望输入（注入步骤 prompt）
        expected_output: 期望输出（注入步骤 prompt）
        depends_on: 依赖的前序步骤序号；``None`` 表示无依赖
        thinking_level: 本步模型思考深度（``low`` / ``medium`` / ``high``）
    """

    step_number: int
    description: str
    required_toolboxes: list[str] = field(default_factory=list)
    expected_input: str = ""
    expected_output: str = ""
    depends_on: int | None = None
    thinking_level: ThinkingLevel | None = None


@dataclass
class PlanChunk:
    """计划分块：任务过大时将步骤分组执行。

    由 ``contextStrategy.chunks`` 解析；每块可附带 ``chunk_system_prompt`` 注入执行上下文。
    与 ``SuggestedConfig.chunk_execution`` 配合：有 ``chunks`` 时分块执行，否则仅作标记。

    Attributes:
        chunk_number: 分块序号（从 1 起）
        steps: 该分块包含的步骤
        estimated_tokens: 预估 token 消耗
        chunk_system_prompt: 该分块的上下文增强（注入 user 消息）
    """

    chunk_number: int
    steps: list[PlanStep] = field(default_factory=list)
    estimated_tokens: int = 0
    chunk_system_prompt: str = ""


@dataclass
class SuggestedConfig:
    """规划器推荐的运行时配置。

    Phase 1 生成，Phase 2 前由 :func:`miniagent.core.agent._merge_plan_suggested_config`
    与默认配置、用户配置合并。

    Attributes:
        max_turns: 推荐的最大 ReAct 轮数
        tool_timeout: 推荐的单工具超时（秒）
        context_overflow_strategy: 上下文溢出策略（``summarize`` / ``truncate`` / ``error``）
        tool_selection_strategy: 工具选择策略
        model_overrides: 模型参数覆盖（如 temperature）
        thinking_level: 全局推荐思考档位（``low`` / ``medium`` / ``high``）
        chunk_execution: 是否启用分块执行语义
        chunk_token_budget: 分块 token 预算（收紧上下文压缩阈值）
        parallelism: 并行策略
        risk_level: 风险等级
    """

    max_turns: int | None = None
    tool_timeout: int | None = None
    context_overflow_strategy: str | None = None
    tool_selection_strategy: str | None = None
    model_overrides: dict[str, object] | None = None
    thinking_level: ThinkingLevel | None = None
    chunk_execution: bool = False
    chunk_token_budget: int | None = None
    parallelism: Parallelism | None = None
    risk_level: RiskLevel | None = None


@dataclass
class EstimatedCost:
    """成本预估（展示与高风险确认用，不触发计费拦截）。

    Attributes:
        input_tokens: 预估输入 token
        output_tokens: 预估输出 token
        total_usd: 预估总成本（美元）
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_usd: float = 0.0


@dataclass
class OutputSpec:
    """输出交付规格（注入执行阶段 user context）。

    Attributes:
        language: 输出语言（如 ``zh-CN``、``en-US``）
        format: 输出格式
        expected_deliverable: 期望交付物描述
    """

    language: str = "zh-CN"
    format: OutputFormat = "markdown"
    expected_deliverable: str = ""


@dataclass
class FallbackPlan:
    """执行失败时的降级策略。

    当 Phase 2 返回带 ``WARNING_PREFIX`` 的结果且 ``degrade_to_simple`` 为真时，
    Agent 会以 ``degraded_max_turns`` 重试一次非分步 ReAct 循环。

    Attributes:
        degrade_to_simple: 是否在失败时降级为简单单轮执行
        degraded_max_turns: 降级模式下的最大轮数
    """

    degrade_to_simple: bool = False
    degraded_max_turns: int = 10


@dataclass
class ContextStrategy:
    """上下文策略。

    ``mode`` 可映射为 ``context_overflow_strategy``；``chunked`` 且 ``chunks`` 非空时
    由 Executor 按分块迭代步骤。

    Attributes:
        mode: 策略模式
        chunks: 分块列表（``mode=chunked`` 时由 Planner 填充）
        reason: 策略说明（日志与计划展示）
    """

    mode: ContextMode = "normal"
    chunks: list[PlanChunk] | None = None
    reason: str = ""


@dataclass
class EstimatedTokens:
    """Token 消耗预估。

    Attributes:
        prompt_tokens: 预估 prompt token
        completion_tokens: 预估 completion token
        tool_result_tokens: 预估工具结果 token
        total: 合计
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_result_tokens: int = 0
    total: int = 0


@dataclass
class StructuredPlan:
    """结构化执行计划（Phase 1 的产物）。

    由 LLM 根据用户需求和可用工具箱生成，经 :mod:`miniagent.core.planner` 解析与规范化。

    Attributes:
        summary: 计划摘要
        steps: 执行步骤列表
        required_toolboxes: 全局需要的工具箱 ID
        suggested_config: 推荐运行时配置
        estimated_tokens: Token 消耗预估
        context_strategy: 上下文策略
        requires_confirmation: 是否需要用户确认（高风险）
        confirmation_message: 确认提示文案
        risk_level: 计划级风险等级
        estimated_cost: 成本预估
        output_spec: 输出规格
        fallback_plan: 降级计划
    """

    summary: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    required_toolboxes: list[str] = field(default_factory=list)
    suggested_config: SuggestedConfig = field(default_factory=SuggestedConfig)
    estimated_tokens: EstimatedTokens = field(default_factory=EstimatedTokens)
    context_strategy: ContextStrategy = field(default_factory=ContextStrategy)
    requires_confirmation: bool = False
    confirmation_message: str | None = None
    risk_level: RiskLevel = "low"
    estimated_cost: EstimatedCost = field(default_factory=EstimatedCost)
    output_spec: OutputSpec = field(default_factory=OutputSpec)
    fallback_plan: FallbackPlan = field(default_factory=FallbackPlan)


__all__ = [
    "ContextMode",
    "ContextStrategy",
    "EstimatedCost",
    "EstimatedTokens",
    "FallbackPlan",
    "OutputFormat",
    "OutputSpec",
    "Parallelism",
    "PlanChunk",
    "PlanStep",
    "RiskLevel",
    "StructuredPlan",
    "SuggestedConfig",
    "ThinkingLevel",
]
