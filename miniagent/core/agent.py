"""Mini Agent Python — Agent 编排层（两阶段主入口）

两阶段架构的主入口：
- **Phase 1（Planning）**：调用 :mod:`miniagent.core.planner`，产出 ``StructuredPlan``；在
  ``skip_planning``、无工具箱、或任务分类为「简单」时可跳过并回落默认计划。
- **Phase 2（Execution）**：调用 :mod:`miniagent.core.executor` 的 ReAct 循环直至无工具调用或达上限。

**边界**：本模块不处理 stdin/stdout、消息队列或飞书 HTTP；仅编排 LLM 与工具。通道相关回调通过
``on_thinking`` / ``on_tool_call`` 等注入，由 :class:`miniagent.engine.engine.UnifiedEngine` 等上层接线。
规划可见输出合并为 ``[评估与计划]`` 流式段；可选关键字参数 ``full_record`` 由引擎用于会话历史全量落盘（见 ``miniagent.core.thinking_callback.invoke_on_thinking``）。

**轮数上限（与执行器一致）**：全局 ReAct 上限由 ``agent.max_turns``（默认 400）控制；分步模式下单步上限为 Internal 常量 ``STEP_MAX_TURNS``。规划器给出的建议轮数**不会**把上述硬上限压低。

**导出**：``run_agent``、``run_pipeline``、常量 ``PLANNING_STREAM_HEADER``。

设计背景见 ``docs/ARCHITECTURE.md``（两阶段管线）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.core.executor import execute_plan
from miniagent.core.planner import generate_plan
from miniagent.core.problem_solver import reflect_on_result
from miniagent.core.task_classifier import (
    TaskDifficulty,
    classify_task_difficulty,
    default_step_thinking_for_difficulty,
    exec_merge_for_simple_path,
    planner_merge_for_difficulty,
    task_classifier_enabled,
)
from miniagent.core.thinking_callback import invoke_on_thinking
from miniagent.core.thinking_presets import map_business_depth
from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.infrastructure.monitor import DefaultToolMonitor
from miniagent.security.sandbox import get_default_workspace
from miniagent.types.agent import PipelineResult, PipelineStep, ToolMonitorProtocol
from miniagent.types.confirmation import ConfirmationRequest, ConfirmationStage
from miniagent.types.error_prefix import WARNING_PREFIX
from miniagent.types.planning import (
    ContextStrategy,
    EstimatedTokens,
    StructuredPlan,
    SuggestedConfig,
)
from miniagent.types.protocols import OnThinkingCallback, OnToolFinishCallback
from miniagent.types.tool import Toolbox, ToolContext, ToolRegistryProtocol

_logger = get_logger(__name__)


def _announce_difficulty_and_plan_enabled() -> bool:
    """是否向用户展示任务难度与规划摘要（默认开启）。"""
    from miniagent.core.constants import EXECUTION_ANNOUNCE_DIFFICULTY

    return EXECUTION_ANNOUNCE_DIFFICULTY


_DIFFICULTY_LABELS = {
    "simple": "简单",
    "normal": "一般",
    "medium": "中等",
    "complex": "复杂",
}


def _format_task_difficulty(difficulty: Any, *, display: bool = False) -> str:
    """Format task difficulty; *display=True* returns the short CLI/Feishu card line."""
    key = getattr(difficulty, "value", str(difficulty))
    zh = _DIFFICULTY_LABELS.get(key, key)
    if display:
        return f"**难度** {zh}（{key}）"
    return (
        f"任务难度：{zh}（{key}）\n将据此调整规划与执行的思考深度（若已启用分类器）。"
    )


def _skip_structured_plan_reason(
    *,
    no_toolboxes: bool,
    user_skip_planning: bool,
    simple_classified: bool,
) -> str:
    """Human-readable single reason; callers ensure mutually exclusive typical paths."""
    if no_toolboxes:
        return "原因：无可用工具箱，未调用结构化规划器。"
    if user_skip_planning:
        return "原因：已显式跳过规划（skip_planning），未调用结构化规划器。"
    if simple_classified:
        return "原因：任务难度评估为「简单」，已跳过结构化规划。"
    return "原因：未调用结构化规划器。"


PLANNING_STREAM_HEADER = "[评估与计划]"


def _format_plan_display_short(
    plan: StructuredPlan,
    *,
    from_llm_planner: bool,
    no_toolboxes: bool = False,
    user_skip_planning: bool = False,
    simple_classified: bool = False,
) -> str:
    """执行计划展示用精简 Markdown（无逐步预期输入/产出长段）。"""
    if not from_llm_planner:
        reason = _skip_structured_plan_reason(
            no_toolboxes=no_toolboxes,
            user_skip_planning=user_skip_planning,
            simple_classified=simple_classified,
        )
        return (
            "（已跳过结构化规划）\n"
            + reason
            + f"\n摘要：{(plan.summary or '').strip() or '—'}"
        )
    lines: list[str] = [(plan.summary or "").strip() or "—"]
    if plan.steps:
        lines.append("")
        for i, st in enumerate(plan.steps, start=1):
            desc = (st.description or "").strip() or "—"
            lines.append(f"{i}. {desc}")
    if plan.required_toolboxes:
        lines.append("")
        lines.append(f"工具箱：`{', '.join(plan.required_toolboxes)}`")
    return "\n".join(lines)


def _format_plan_message(
    plan: StructuredPlan,
    *,
    from_llm_planner: bool,
    no_toolboxes: bool = False,
    user_skip_planning: bool = False,
    simple_classified: bool = False,
) -> str:
    """Full plan markdown for on_thinking / session history; Feishu caps apply in poll_server."""
    if not from_llm_planner:
        reason = _skip_structured_plan_reason(
            no_toolboxes=no_toolboxes,
            user_skip_planning=user_skip_planning,
            simple_classified=simple_classified,
        )
        return (
            f"执行模式：跳过结构化规划。\n{reason}\n"
            f"摘要：{(plan.summary or '').strip() or '—'}"
        )
    lines: list[str] = [(plan.summary or "").strip() or "—"]
    if plan.steps:
        lines.append("")
        lines.append("步骤概要：")
        for i, st in enumerate(plan.steps, start=1):
            desc = (st.description or "").strip()
            lines.append(f"{i}. {desc}")
            ei = (st.expected_input or "").strip()
            eo = (st.expected_output or "").strip()
            if ei:
                lines.append(f"预期输入：{ei}")
            if eo:
                lines.append(f"预期产出：{eo}")
    if plan.required_toolboxes:
        lines.append("")
        lines.append(f"涉及工具箱：{', '.join(plan.required_toolboxes)}")
    return "\n".join(lines)


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall = Callable[[str, str, str], None]
OnToolFinish = OnToolFinishCallback
OnPlan = Callable[[StructuredPlan], Awaitable[bool]]
OnThinking = OnThinkingCallback


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
    on_tool_finish: OnToolFinish | None = None,
    on_plan: OnPlan | None = None,
    on_thinking: OnThinking | None = None,
    clawhub: Any | None = None,
    memory_store: Any | None = None,
    activity_log: Any | None = None,
    keyword_index: Any | None = None,
    client: AsyncOpenAI | None = None,
    clarifier: Any | None = None,
    session_key: str | None = None,
    confirmation_channel: Any | None = None,
    engine: Any | None = None,
) -> str:
    """运行 Agent（两阶段模式）。

    Phase 1: 规划（可跳过）
    Phase 2: ReAct 循环执行

    当提供 ``on_thinking`` 且 Internal 常量 ``EXECUTION_ANNOUNCE_DIFFICULTY`` 为真（默认开启）时，
    将「评估任务难度 → 难度结论 → 执行计划」合并为同一条流式思考（header ``[评估与计划]``）；展示为精简文案，
    完整 Markdown 通过可选关键字 ``full_record`` 写入会话历史（由 :class:`~miniagent.engine.engine.UnifiedEngine` 接线）。
    设为 ``0`` 可关闭上述推送。

    Args:
        user_input: 用户的原始需求
        registry: 工具注册表
        monitor: 性能监控器（默认创建新实例）
        toolboxes: 可用工具箱列表（空则跳过规划）
        agent_config: Agent 配置覆盖
        system_prompt: 自定义系统提示词
        skip_planning: 跳过规划阶段
        on_tool_call: 工具调用回调
        on_tool_finish: 每个工具执行后的异步回调（名称、参数 JSON、完整输出、是否成功）。
            执行器在签名支持时会额外传入 ``thinking_header``（当前执行阶段标签，如 ``[执行]`` 或 ``[步骤 1/3] …``），供飞书等同段合并展示。
            会话 ``history.json`` 中的工具全文块依赖此回调；不传则不会落盘工具输出。
            ``UnifiedEngine.run_agent_with_thinking`` 已默认传入。
        on_plan: 计划确认回调（返回 True 批准执行）
        on_thinking: 思考过程回调（含难度/规划可见输出与执行阶段流式思考）
        session_key: 会话标识符（用于记忆加载）

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

    # ── Phase 0: 任务难度分类 ──
    difficulty = TaskDifficulty.NORMAL
    effective_skip = skip_planning

    planning_hist = ""
    planning_display = ""

    if toolboxes and not skip_planning and task_classifier_enabled():
        difficulty = await classify_task_difficulty(
            user_input,
            [t.id for t in toolboxes],
            client=client,
            agent_config=merged_config,
        )
        if difficulty == TaskDifficulty.SIMPLE:
            effective_skip = True
        if _announce_difficulty_and_plan_enabled() and on_thinking:
            diff_msg = _format_task_difficulty(difficulty)
            diff_disp = _format_task_difficulty(difficulty, display=True)
            planning_hist = diff_msg
            planning_display = diff_disp
            await invoke_on_thinking(
                on_thinking,
                planning_display,
                True,
                PLANNING_STREAM_HEADER,
                full_record=planning_hist,
            )

    # ── Phase 0.5: 需求澄清（按难度条件执行）──
    # 简单任务：不澄清；一般任务：最多澄清 1 个问题；复杂任务：完整澄清
    clarifier_enabled = get_config("features.requirement_clarify", True)
    clarified_text = ""
    if clarifier_enabled and clarifier is not None and difficulty != TaskDifficulty.SIMPLE:
        # 交互追问回调（通过确认侧通道阻塞等待用户回答）
        async def _ask_user_for_clarification(question: str) -> str:
            if confirmation_channel is None or on_thinking is None:
                _logger.warning("需求澄清: confirmation_channel 或 on_thinking 未设置，跳过追问")
                return ""
            _logger.info("需求澄清: 向用户发送追问: %s", question[:80])
            # 直接发送问题，不含 .adjust 提示；用户直接回复即可
            await invoke_on_thinking(on_thinking, f"❓ {question}", True, "[需求澄清]")
            req = ConfirmationRequest(stage=ConfirmationStage.CLARIFICATION, content=question)
            _logger.info("需求澄清: 已发送 ConfirmationRequest，等待用户回复...")
            result = await confirmation_channel.request_confirmation(req)
            answer = (result.adjustment or "").strip() if result else ""
            _logger.info("需求澄清: 收到用户回复: %s", answer[:80] if answer else "(空)")
            # 将用户回复展示到 CLI/飞书，保持上下文完整
            if answer and on_thinking:
                await invoke_on_thinking(
                    on_thinking,
                    f"用户回复：{answer}",
                    True,
                    "[需求澄清]",
                )
            return answer

        # 即时反馈：在 LLM 调用前让用户看到提示，避免"沉默太久"
        if on_thinking:
            try:
                await invoke_on_thinking(
                    on_thinking,
                    "正在分析需求，识别模糊表述与边界条件…",
                    True,
                    "[需求澄清]",
                )
            except Exception as e:
                _logger.debug("调用thinking回调失败: %s", e)
        try:
            # 一般任务最多 1 问；中等任务最多 2 问；复杂任务最多 3 问
            if difficulty == TaskDifficulty.NORMAL:
                base_questions = 1
            elif difficulty == TaskDifficulty.MEDIUM:
                base_questions = 2
            else:
                base_questions = 3
            max_questions = min(base_questions, int(get_config("agent.max_questions", 3)))
            clarified = await clarifier.clarify(
                user_input,
                ask_user=_ask_user_for_clarification,
                memory_store=memory_store,
                session_key=session_key,
                max_questions=max_questions,
            )
            # 构建完整的澄清信息传递给规划器和执行器
            # 使用 to_system_prompt 生成包含目标、约束、输出规格、示例的完整提示词
            clarified_text = getattr(clarified, "clarified_goal", "") or ""
            if clarified:
                # 将完整澄清结果注入到 user_input，确保用户补充、输出规格等生效
                full_clarification = clarifier.to_system_prompt(clarified)
                user_input = f"{user_input}\n\n{full_clarification}"
                if _announce_difficulty_and_plan_enabled() and on_thinking:
                    try:
                        await invoke_on_thinking(
                            on_thinking,
                            f"需求已澄清：{clarified_text[:80]}",
                            True,
                            "[需求澄清]",
                            full_record=full_clarification,
                        )
                    except Exception as e:
                        _logger.debug("澄清结果推送失败（非关键）: %s", e)
        except Exception as e:
            _logger.warning("需求澄清失败: %s", e)

    # ── Phase 1: 规划/默认计划 ──
    plan: StructuredPlan
    from_llm_planner = False

    # ── 直接执行模式 ──
    if effective_skip or not toolboxes:
        plan = _create_default_plan()
        if toolboxes and effective_skip and difficulty == TaskDifficulty.SIMPLE:
            merged_config = merge_agent_config(
                merged_config,
                {"model_overrides": exec_merge_for_simple_path()},
            )
    else:
        # ── Phase 1: 规划 ──
        from_llm_planner = True
        plan = await generate_plan(
            user_input,
            toolboxes,
            merged_config.log_file,
            client=client,
            agent_config=merged_config,
            registry=registry,
            planner_model_overrides=planner_merge_for_difficulty(difficulty),
            default_step_thinking=default_step_thinking_for_difficulty(difficulty),
        )

        # 合并规划器的建议配置（suggested_config）到运行配置
        # 规划器根据任务复杂度生成建议值，Agent 只会"抬高"下限，不会压低用户硬上限
        if plan.suggested_config:
            sc = plan.suggested_config
            overrides: dict[str, Any] = {}
            # max_turns: 取用户设置与规划建议的较大值（保证规划器建议不压低上限）
            if sc.max_turns is not None:
                overrides["max_turns"] = max(merged_config.max_turns, sc.max_turns)
            # tool_timeout: 规划器可调整单工具超时
            if sc.tool_timeout is not None:
                overrides["tool_timeout"] = sc.tool_timeout
            # risk_level: 规划器可调整风险等级
            if sc.risk_level is not None:
                overrides["risk_level"] = sc.risk_level
            # context_overflow_strategy: 上下文溢出处理策略
            if sc.context_overflow_strategy is not None:
                overrides["context_overflow_strategy"] = sc.context_overflow_strategy
            # tool_selection_strategy: 工具选择策略
            if sc.tool_selection_strategy is not None:
                overrides["tool_selection_strategy"] = sc.tool_selection_strategy
            # thinking 参数：从业务档位映射到模型参数
            mo: dict[str, Any] = {}
            if sc.thinking_level:
                tl, tb = map_business_depth(sc.thinking_level)
                mo["thinking_level"] = tl  # 模型参数名
                mo["thinking_budget"] = tb  # token 预算
            if sc.model_overrides:
                mo.update(sc.model_overrides)  # 合规划器额外模型参数
            if mo:
                overrides["model_overrides"] = mo
            # parallelism: 并行策略（sequential=禁用并行，safe-parallel/full-parallel=启用）
            if sc.parallelism == "sequential":
                overrides["allow_parallel_tools"] = False
            elif sc.parallelism in ("safe-parallel", "full-parallel"):
                overrides["allow_parallel_tools"] = True
            if overrides:
                merged_config = merge_agent_config(merged_config, overrides)

        if merged_config.risk_level is None and plan.risk_level:
            merged_config = merge_agent_config(merged_config, {"risk_level": plan.risk_level})

        if merged_config.debug:
            _logger.info("规划结果: %s", plan.summary)
            _logger.debug("工具箱: %s", ", ".join(plan.required_toolboxes))
            _logger.debug("预估 token: %d", plan.estimated_tokens.total)
            _logger.debug("风险等级: %s", plan.risk_level)

        # 高风险操作需要用户确认（on_plan 回调或确认侧通道）
        if plan.requires_confirmation and on_plan:
            if on_thinking:
                try:
                    await invoke_on_thinking(
                        on_thinking,
                        f"{WARNING_PREFIX} 高风险操作，请确认执行计划。输入 .confirm 同意，.reject 拒绝，.adjust 调整。",
                        True,
                        "[等待确认]",
                    )
                except Exception as e:
                    _logger.debug("等待确认推送失败（非关键）: %s", e)
            approved = await on_plan(plan)
            if not approved:
                return f"{WARNING_PREFIX} 操作已取消"

    if _announce_difficulty_and_plan_enabled() and on_thinking:
        plan_msg = _format_plan_message(
            plan,
            from_llm_planner=from_llm_planner,
            no_toolboxes=len(toolboxes) == 0,
            user_skip_planning=skip_planning,
            simple_classified=(
                bool(toolboxes) and not skip_planning and difficulty == TaskDifficulty.SIMPLE
            ),
        )
        plan_disp = _format_plan_display_short(
            plan,
            from_llm_planner=from_llm_planner,
            no_toolboxes=len(toolboxes) == 0,
            user_skip_planning=skip_planning,
            simple_classified=(
                bool(toolboxes) and not skip_planning and difficulty == TaskDifficulty.SIMPLE
            ),
        )
        # 关键修复：使用 reset=True 清除已有难度内容，避免重复显示
        # 需求澄清后的第二次 [评估与计划] 应只显示规划概要
        planning_hist = plan_msg
        planning_display = plan_disp
        await invoke_on_thinking(
            on_thinking,
            planning_display,
            True,
            PLANNING_STREAM_HEADER,
            full_record=planning_hist,
            reset=True,
        )

    # ── Phase 2: 执行 ──
    reply = await execute_plan(
        plan,
        user_input,
        registry,
        monitor,
        merged_config,
        on_tool_call,
        on_thinking,
        on_tool_finish=on_tool_finish,
        system_prompt=system_prompt,
        clawhub=clawhub,
        memory_store=memory_store,
        activity_log=activity_log,
        keyword_index=keyword_index,
        client=client,
    )

    # ── Phase 3: 反思评估 ──
    reflection_enabled = get_config("features.reflection", True)
    if reflection_enabled:
        reflection = await reflect_on_result(user_input, reply, client=client, on_thinking=None)
        # 存储到引擎供飞书发送独立质量卡片
        if engine is not None:
            engine._last_reflection = reflection
        # CLI 侧：在回复末尾追加质量评估尾部（飞书侧会发送独立卡片，不再依赖此处）
        status = "质量评估通过" if reflection.acceptable else "质量评估需改进"
        reflection_footer = f"\n\n---\n🤖 {status} | 质量评分 {reflection.quality_score:.1f}"
        if reflection.suggestions:
            reflection_footer += "\n\n建议：\n" + "\n".join(
                f"- {s}" for s in reflection.suggestions[:5]
            )
        reply = reply + reflection_footer

    return reply


# ─── 线性管线执行器 ─────────────────────────────────────


async def run_pipeline(
    steps: list[PipelineStep],
    registry: ToolRegistryProtocol,
    context: ToolContext | None = None,
    on_tool_call: OnToolCall | None = None,
    *,
    clawhub: Any | None = None,
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
            clawhub=clawhub,
        )

    for step in steps:
        tool = registry.get(step.tool)
        if tool is None:
            err_result = {"success": False, "content": f"{WARNING_PREFIX} 未知工具: {step.tool}"}
            results.append({"tool": step.tool, "args": step.args, "result": err_result})
            return PipelineResult(steps=results, final_content=err_result["content"], success=False)

        result = await tool.handler(step.args, context)
        results.append(
            {
                "tool": step.tool,
                "args": step.args,
                "result": {"success": result.success, "content": result.content},
            }
        )
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
        suggested_config=SuggestedConfig(max_turns=None, tool_timeout=30, risk_level="low"),
        estimated_tokens=EstimatedTokens(),
        context_strategy=ContextStrategy(mode="normal", reason="跳过规划"),
        requires_confirmation=False,
        risk_level="low",
    )


__all__ = ["run_agent", "run_pipeline", "PLANNING_STREAM_HEADER"]
