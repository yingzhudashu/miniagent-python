"""Problem Solver — 四阶段问题求解器。

本模块将**解题四阶段法**（理解 → 计划 → 执行 → 反思）融入 Agent 流程，
作为现有 ``run_agent()`` 两阶段管线的增强版。

四阶段流程：
1. **Understand（理解问题）**：调用 LLM 分析已知条件、未知因素、约束和目标
2. **Plan（制定计划）**：调用现有 ``generate_plan()`` 生成 ``StructuredPlan``
3. **Execute（执行推导）**：调用现有 ``execute_plan()`` 执行 ReAct 循环
   （可注入反馈控制器、状态观测器、自适应策略）
4. **Reflect（反思评估）**：调用 LLM 自评估结果质量，判定是否可接受

设计哲学（解题四阶段法）：
- 理解阶段：明确已知与未知，识别约束条件
- 计划阶段：构建逻辑路径，复用现有能力
- 执行阶段：验证推导过程，确保每步正确
- 反思阶段：评估结果，泛化经验

与 ``run_agent()`` 的关系：
``ProblemSolver.solve()`` 是 ``run_agent()`` 的上层封装，
内部调用 ``generate_plan()`` + ``execute_plan()``，
额外增加了 Understand 和 Reflect 两个 LLM 调用阶段。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.core.executor import execute_plan
from miniagent.core.llm_json import llm_json
from miniagent.core.planner import generate_plan
from miniagent.core.thinking_callback import invoke_on_thinking
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.types.agent import ToolMonitorProtocol
from miniagent.types.tool import ToolRegistryProtocol

_logger = get_logger(__name__)


@dataclass
class ProblemAnalysis:
    """阶段 1：理解问题 — LLM 分析结果。"""

    knowns: list[str] = field(default_factory=list)       # 已知条件
    unknowns: list[str] = field(default_factory=list)     # 未知条件
    constraints: list[str] = field(default_factory=list)  # 约束
    goal: str = ""                                        # 目标描述


@dataclass
class ReflectionResult:
    """阶段 3：反思 — 结果评估。"""

    acceptable: bool            # 结果是否可接受
    quality_score: float        # 0-1 质量评分
    issues: list[str] = field(default_factory=list)       # 发现的问题
    suggestions: list[str] = field(default_factory=list)  # 改进建议


PROBLEM_ANALYSIS_PROMPT = """你是一个问题分析专家。请分析以下用户输入，识别已知条件、未知因素、约束和目标。

请以 JSON 格式返回分析结果：
{
  "knowns": ["已知条件1", "已知条件2"],
  "unknowns": ["未知因素1", "未知因素2"],
  "constraints": ["约束1", "约束2"],
  "goal": "目标描述"
}

只返回 JSON，不要其他文字。"""

REFLECTION_PROMPT = """你是一个结果评估专家。请评估以下任务的完成质量。

用户原始输入：
{user_input}

Agent 执行结果：
{reply}

请以 JSON 格式返回评估：
{{
  "acceptable": true/false,
  "quality_score": 0.0-1.0,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}}

只返回 JSON，不要其他文字。"""


async def reflect_on_result(
    user_input: str,
    reply: str,
    client: AsyncOpenAI | None = None,
    on_thinking: Any | None = None,
) -> ReflectionResult:
    """Phase 3: 反思 — LLM 自评估结果质量。

    模块级函数，供 ``run_agent()`` 和 ``ProblemSolver`` 复用。

    Args:
        user_input: 用户原始输入
        reply: Agent 执行结果
        client: LLM 客户端
        on_thinking: 思考过程回调

    Returns:
        反思评估结果
    """
    if on_thinking:
        await invoke_on_thinking(on_thinking, "评估结果质量...", True, "[反思评估]")

    result = await llm_json(
        prompt=REFLECTION_PROMPT.format(user_input=user_input, reply=reply),
        system="你是一个结果评估专家。请评估任务完成质量。只返回 JSON。",
        client=client,
    )

    reflection = ReflectionResult(
        acceptable=result.get("acceptable", True),
        quality_score=float(result.get("quality_score", 0.5)),
        issues=result.get("issues", []),
        suggestions=result.get("suggestions", []),
    )

    if on_thinking:
        status = "可接受" if reflection.acceptable else "需改进"
        await invoke_on_thinking(
            on_thinking,
            f"质量评分 {reflection.quality_score:.1f}，判定：{status}",
            True,
            "[反思评估]",
        )

    return reflection


@dataclass
class ProblemSolver:
    """四阶段问题求解器。

    Phase 1 (Understand)：调用 LLM 分析已知/未知/约束/目标
    Phase 2 (Plan)：调用现有 generate_plan() 生成 StructuredPlan
    Phase 3 (Execute)：调用现有 execute_plan() 执行
    Phase 4 (Reflect)：LLM 自评估结果质量，判定是否可接受

    Args:
        max_iterations: 最大迭代次数（Reflect 不通过时重试，默认 1 = 不重试）
    """

    max_iterations: int = 1

    async def solve(
        self,
        user_input: str,
        registry: ToolRegistryProtocol,
        monitor: ToolMonitorProtocol | None = None,
        toolboxes: list | None = None,
        agent_config: dict[str, Any] | None = None,
        system_prompt: str | None = None,
        skip_planning: bool = False,
        on_tool_call: Any | None = None,
        on_tool_finish: Any | None = None,
        on_thinking: Any | None = None,
        client: AsyncOpenAI | None = None,
        clarifications: dict[str, Any] | None = None,
        feedback_controller: Any | None = None,
        state_observer: Any | None = None,
        adaptive_policy: Any | None = None,
    ) -> tuple[str, ReflectionResult | None]:
        """执行四阶段求解流程。

        Args:
            user_input: 用户原始需求
            registry: 工具注册表
            monitor: 性能监控器
            toolboxes: 可用工具箱
            agent_config: Agent 配置
            system_prompt: 自定义系统提示词
            skip_planning: 跳过规划
            on_tool_call: 工具调用回调
            on_tool_finish: 工具执行回调
            on_thinking: 思考过程回调
            client: LLM 客户端
            clarifications: 来自需求澄清器的结果（可选）
            feedback_controller: 反馈控制器（可选）
            state_observer: 状态观测器（可选）
            adaptive_policy: 自适应策略（可选）

        Returns:
            (最终回复, 反思结果) 的元组
        """
        if monitor is None:
            monitor = DefaultToolMonitor()
        if toolboxes is None:
            toolboxes = []

        base_config = get_default_agent_config()
        merged_config = merge_agent_config(base_config, agent_config or {})

        # Phase 1: Understand — 问题分析
        analysis = await self._analyze_problem(
            user_input, client, clarifications, on_thinking,
            memory_store=getattr(merged_config, 'session_registry', None),
            session_key=getattr(merged_config, 'session_key', None),
        )

        # 构建增强输入（含澄清结果和分析）
        enhanced_input = user_input
        if clarifications:
            clarified_goal = clarifications.get("clarified_goal", "")
            if clarified_goal:
                enhanced_input = f"{user_input}\n\n## 澄清后的目标\n{clarified_goal}"
            boundary = clarifications.get("boundary_conditions", [])
            if boundary:
                enhanced_input += "\n\n## 约束\n" + "\n".join(f"- {b}" for b in boundary)
        if analysis and (analysis.unknowns or analysis.constraints):
            parts: list[str] = [enhanced_input, "\n## 问题分析"]
            if analysis.constraints:
                parts.append("约束：\n" + "\n".join(f"- {c}" for c in analysis.constraints))
            if analysis.unknowns:
                parts.append("待确认：\n" + "\n".join(f"- {u}" for u in analysis.unknowns))
            enhanced_input = "\n".join(parts)

        # Phase 2 & 3: Plan + Execute — 使用现有 run_agent 路径
        # 构建增强 system prompt
        enhanced_system = system_prompt
        if analysis and analysis.goal:
            prefix = f"## 任务目标\n{analysis.goal}\n\n"
            enhanced_system = (prefix + (system_prompt or "")) if system_prompt else prefix

        plan = await generate_plan(
            enhanced_input,
            toolboxes,
            merged_config.log_file,
            client=client,
            agent_config=merged_config,
        )

        # Phase 3: Execute
        reply = await execute_plan(
            plan,
            enhanced_input,
            registry=registry,
            monitor=monitor,
            agent_config=merged_config,
            on_tool_call=on_tool_call,
            on_thinking=on_thinking,
            on_tool_finish=on_tool_finish,
            system_prompt=enhanced_system,
            client=client,
            feedback_controller=feedback_controller,
            state_observer=state_observer,
            adaptive_policy=adaptive_policy,
        )

        # Phase 4: Reflect — 结果评估（可选）
        reflection: ReflectionResult | None = None
        if self.max_iterations >= 1:
            reflection = await reflect_on_result(
                user_input, reply, client=client, on_thinking=on_thinking,
            )

        return reply, reflection

    async def _analyze_problem(
        self,
        user_input: str,
        client: AsyncOpenAI | None,
        clarifications: dict[str, Any] | None,
        on_thinking: Any | None,
        *,
        memory_store: Any | None = None,
        session_key: str | None = None,
    ) -> ProblemAnalysis | None:
        """Phase 1: 理解问题 — 调用 LLM 分析问题。"""
        if on_thinking:
            await invoke_on_thinking(on_thinking, "分析问题...", True, "[理解问题]")

        # 加载会话记忆（让问题分析看到历史上下文）
        memory_context = ""
        if memory_store and session_key:
            try:
                from miniagent.memory.store import format_memory_for_prompt

                memory = await memory_store.load(session_key)
                memory_context = format_memory_for_prompt(memory)
            except Exception:
                pass

        context = user_input
        if clarifications:
            context = json.dumps(clarifications, ensure_ascii=False) + "\n\n" + user_input
        if memory_context:
            context = memory_context + context

        result = await llm_json(
            prompt=context,
            system=PROBLEM_ANALYSIS_PROMPT,
            client=client,
        )

        analysis = ProblemAnalysis(
            knowns=result.get("knowns", []),
            unknowns=result.get("unknowns", []),
            constraints=result.get("constraints", []),
            goal=result.get("goal", ""),
        )

        if on_thinking:
            await invoke_on_thinking(
                on_thinking,
                f"分析完成：已知 {len(analysis.knowns)} 项，未知 {len(analysis.unknowns)} 项",
                False,
                "[理解问题]",
            )

        return analysis


__all__ = ["ProblemSolver", "ProblemAnalysis", "ReflectionResult", "reflect_on_result", "REFLECTION_PROMPT"]
