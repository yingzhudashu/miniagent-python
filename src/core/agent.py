"""Mini Agent Python — Agent 编排层 (Phase 4)

两阶段架构的主入口：
- Phase 1: Planning（规划） — LLM 分析需求，生成 StructuredPlan
- Phase 2: Execution（执行） — ReAct 循环执行计划

导出：
- run_agent(): 两阶段主入口
- run_pipeline(): 线性管线执行器（无 LLM 循环）
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from src.types.planning import StructuredPlan, PlanStep, SuggestedConfig, EstimatedTokens, ContextStrategy
from src.types.config import AgentConfig
from src.types.tool import Toolbox, ToolContext, ToolRegistryProtocol
from src.types.agent import ToolMonitorProtocol, PipelineStep, PipelineResult
from src.core.config import get_default_agent_config, merge_agent_config
from src.core.logger import get_logger

_logger = get_logger(__name__)
from src.core.executor import execute_plan
from src.core.monitor import DefaultToolMonitor
from src.security.sandbox import get_default_workspace

# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]
OnPlan = Callable[[StructuredPlan], Awaitable[bool]]


# ─── 主入口 ──────────────────────────────────────────────

async def run_agent(
    user_input: str,
    *,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol | None = None,
    toolboxes: list[Toolbox] | None = None,
    agent_config: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    skip_planning: bool = False,
    on_tool_call: OnToolCall | None = None,
    on_plan: OnPlan | None = None,
) -> str:
    """运行 Agent（两阶段模式）。

    Phase 1: 规划（可跳过）
    Phase 2: ReAct 循环执行

    Args:
        user_input: 用户的原始需求
        registry: 工具注册表
        monitor: 性能监控器（默认创建新实例）
        toolboxes: 可用工具箱列表（空则跳过规划）
        agent_config: Agent 配置覆盖
        system_prompt: 自定义系统提示词
        skip_planning: 跳过规划阶段
        on_tool_call: 工具调用回调
        on_plan: 计划确认回调（返回 True 批准执行）

    Returns:
        Agent 的最终回复文本
    """
    if monitor is None:
        monitor = DefaultToolMonitor()
    if toolboxes is None:
        toolboxes = []

    # ── 合并配置 ──
    base_config = get_default_agent_config()
    merged_config = merge_agent_config(base_config, agent_config or {})

    plan: StructuredPlan

    # ── 直接执行模式 ──
    if skip_planning or not toolboxes:
        plan = _create_default_plan()
    else:
        # ── Phase 1: 规划 ──
        from src.core.planner import generate_plan

        plan = await generate_plan(user_input, toolboxes, merged_config.log_file)

        # 合并规划器的建议配置
        if plan.suggested_config:
            sc = plan.suggested_config
            overrides: dict[str, Any] = {}
            if sc.max_turns is not None:
                overrides["max_turns"] = sc.max_turns
            if sc.tool_timeout is not None:
                overrides["tool_timeout"] = sc.tool_timeout
            if sc.risk_level is not None:
                overrides["risk_level"] = sc.risk_level
            if overrides:
                merged_config = merge_agent_config(merged_config, overrides)

        if merged_config.debug:
            _logger.info("规划结果: %s", plan.summary)
            _logger.debug("工具箱: %s", ', '.join(plan.required_toolboxes))
            _logger.debug("预估 token: %d", plan.estimated_tokens.total)
            _logger.debug("风险等级: %s", plan.risk_level)

        # 高风险操作需要用户确认
        if plan.requires_confirmation and on_plan:
            approved = await on_plan(plan)
            if not approved:
                return "⚠️ 操作已取消"

    # ── Phase 2: 执行 ──
    return await execute_plan(plan, user_input, registry, monitor, merged_config, on_tool_call)


# ─── 线性管线执行器 ─────────────────────────────────────

async def run_pipeline(
    steps: list[PipelineStep],
    registry: ToolRegistryProtocol,
    context: ToolContext | None = None,
    on_tool_call: OnToolCall | None = None,
) -> PipelineResult:
    """运行管线（线性工具执行器，无 LLM 循环）。

    与 run_agent 的区别：
    - run_agent: ReAct 循环，LLM 自主决定工具调用顺序
    - run_pipeline: 线性执行，预先定义好工具调用序列

    适用场景：预定义自动化流程、确定性操作、批量文件处理。
    """
    results: list[dict[str, Any]] = []
    pipeline_content = ""

    if context is None:
        workspace = get_default_workspace()
        context = ToolContext(
            cwd=workspace,
            allowed_paths=[workspace],
            permission="allowlist",
        )

    for step in steps:
        tool = registry.get(step.tool)
        if tool is None:
            err_result = {"success": False, "content": f"⚠️ 未知工具: {step.tool}"}
            results.append({"tool": step.tool, "args": step.args, "result": err_result})
            return PipelineResult(steps=results, final_content=err_result["content"], success=False)

        result = await tool.handler(step.args, context)
        results.append({"tool": step.tool, "args": step.args, "result": {"success": result.success, "content": result.content}})
        pipeline_content += result.content + "\n"

        if on_tool_call:
            on_tool_call(step.tool, json.dumps(step.args), result.content)

    return PipelineResult(steps=results, final_content=pipeline_content.strip(), success=True)


# ─── 内部辅助 ────────────────────────────────────────────

def _create_default_plan() -> StructuredPlan:
    """创建默认计划（直接执行模式）。"""
    return StructuredPlan(
        summary="直接执行模式",
        steps=[],
        required_toolboxes=[],
        suggested_config=SuggestedConfig(max_turns=5, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(),
        context_strategy=ContextStrategy(mode="normal", reason="跳过规划"),
        requires_confirmation=False,
        risk_level="low",
    )


__all__ = ["run_agent", "run_pipeline"]
