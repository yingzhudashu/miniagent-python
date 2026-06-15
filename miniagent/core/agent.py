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
from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from miniagent.core.config import get_default_agent_config, merge_agent_config
from miniagent.core.executor import execute_plan
from miniagent.core.planner import generate_plan
from miniagent.core.problem_solver import build_reflection_footer, reflect_on_result
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
from miniagent.types.agent import (
    AgentRunOptions,
    AgentRunResult,
    PipelineResult,
    PipelineStep,
    PipelineStepRecord,
    ToolCallResult,
    ToolMonitorProtocol,
    ToolStats,
)
from miniagent.types.confirmation import ConfirmationRequest, ConfirmationResult, ConfirmationStage
from miniagent.types.config import AgentConfig
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
    """检查是否向用户展示任务难度与规划摘要。

    从常量 EXECUTION_ANNOUNCE_DIFFICULTY 读取配置，用于控制 [评估与计划] 段
    是否在思考流中可见。关闭后规划过程对用户透明，可减少信息过载。

    Returns:
        bool: True 表示展示难度与规划，False 表示静默执行。

    Note:
        该配置影响 on_thinking 回调的 PLANNING_STREAM_HEADER 段推送。
    """
    from miniagent.core.constants import EXECUTION_ANNOUNCE_DIFFICULTY

    return EXECUTION_ANNOUNCE_DIFFICULTY


_DIFFICULTY_LABELS = {
    "simple": "简单",
    "normal": "一般",
    "medium": "中等",
    "complex": "复杂",
}


def _format_task_difficulty(difficulty: Any, *, display: bool = False) -> str:
    """格式化任务难度为可读文本。

    根据 display 参数返回不同格式：
    - display=False: 完整说明（含思考深度调整提示），用于会话历史全量记录
    - display=True: 精简卡片格式（CLI/飞书卡片），仅显示难度标签

    Args:
        difficulty: TaskDifficulty 枚举值或字符串（simple/normal/medium/complex）
        display: 是否返回精简展示格式（默认 False）

    Returns:
        str: 格式化后的难度文本

    Note:
        中文标签映射见 _DIFFICULTY_LABELS 字典。
    """
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
    """生成跳过结构化规划的原因说明。

    根据跳过原因返回用户可读的中文解释，用于展示在 [评估与计划] 段。
    调用方应确保三个布尔标志互斥（仅一个为 True）。

    Args:
        no_toolboxes: 无可用工具箱（纯对话模式）
        user_skip_planning: 用户显式设置 skip_planning=True
        simple_classified: 任务分类器判定为简单任务

    Returns:
        str: 跳过原因的中文说明文本

    Note:
        按优先级检查：no_toolboxes > user_skip_planning > simple_classified。
    """
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
    """格式化执行计划为精简 Markdown（CLI/飞书卡片展示用）。

    根据计划来源（LLM规划器 vs 默认计划）生成不同格式：
    - LLM规划：显示摘要、步骤列表、工具箱
    - 默认计划：显示跳过原因和摘要

    与 _format_plan_message 的区别：此函数省略预期输入/产出细节，适合即时展示。

    Args:
        plan: 结构化执行计划
        from_llm_planner: 是否来自 LLM 规划器（False 表示默认计划）
        no_toolboxes: 是否因无工具箱跳过规划
        user_skip_planning: 是否用户显式跳过规划
        simple_classified: 是否因简单任务跳过规划

    Returns:
        str: 精简格式的计划 Markdown 文本

    Note:
        飞书卡片有字符数限制，此格式确保关键信息可见。
    """
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
    """格式化执行计划为完整 Markdown（会话历史全量记录用）。

    生成包含所有细节的计划文本，用于 on_thinking 回调的 full_record 参数
    和会话历史存储。包含预期输入/产出等完整信息。

    Args:
        plan: 结构化执行计划
        from_llm_planner: 是否来自 LLM 规划器（False 表示默认计划）
        no_toolboxes: 是否因无工具箱跳过规划
        user_skip_planning: 是否用户显式跳过规划
        simple_classified: 是否因简单任务跳过规划

    Returns:
        str: 完整格式的计划 Markdown 文本

    Note:
        飞书通道在 poll_server 中应用字符数限制，此函数不负责截断。
    """
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


def _merge_plan_suggested_config(plan: StructuredPlan, merged_config: AgentConfig) -> AgentConfig:
    """合并规划器建议配置与计划风险等级到运行配置。"""
    config = merged_config
    if plan.suggested_config:
        sc = plan.suggested_config
        overrides: dict[str, Any] = {}
        if sc.max_turns is not None:
            overrides["max_turns"] = max(config.max_turns, sc.max_turns)
        if sc.tool_timeout is not None:
            overrides["tool_timeout"] = sc.tool_timeout
        if sc.risk_level is not None:
            overrides["risk_level"] = sc.risk_level
        if sc.context_overflow_strategy is not None:
            overrides["context_overflow_strategy"] = sc.context_overflow_strategy
        if sc.tool_selection_strategy is not None:
            overrides["tool_selection_strategy"] = sc.tool_selection_strategy
        mo: dict[str, Any] = {}
        if sc.thinking_level:
            tl, tb = map_business_depth(sc.thinking_level)
            mo["thinking_level"] = tl
            mo["thinking_budget"] = tb
        if sc.model_overrides:
            mo.update(sc.model_overrides)
        if mo:
            overrides["model_overrides"] = mo
        if sc.parallelism == "sequential":
            overrides["allow_parallel_tools"] = False
        elif sc.parallelism in ("safe-parallel", "full-parallel"):
            overrides["allow_parallel_tools"] = True
        if overrides:
            config = merge_agent_config(config, overrides)

    if config.risk_level is None and plan.risk_level:
        config = merge_agent_config(config, {"risk_level": plan.risk_level})
    return config


# ─── 回调类型 ────────────────────────────────────────────

OnToolCall: TypeAlias = Callable[[str, str, str], None]
OnToolFinish: TypeAlias = OnToolFinishCallback
OnPlan: TypeAlias = Callable[[StructuredPlan], Awaitable[ConfirmationResult]]
OnThinking: TypeAlias = OnThinkingCallback


# 监控项中不计入 AgentRunResult.total_tool_calls / used_tools
_MONITOR_NON_TOOL_NAMES = frozenset({"llm_response"})


def _build_agent_run_result(reply: str, monitor: ToolMonitorProtocol) -> AgentRunResult:
    """从 monitor 汇总工具统计，构建 ``AgentRunResult``。"""
    all_stats = monitor.get_all_stats()
    tool_stats: dict[str, ToolStats] = {
        name: stats for name, stats in all_stats.items() if name not in _MONITOR_NON_TOOL_NAMES
    }
    used_tools = list(tool_stats.keys())
    total_tool_calls = sum(stats.calls for stats in tool_stats.values())
    return AgentRunResult(
        reply=reply,
        total_tool_calls=total_tool_calls,
        tool_stats=tool_stats,
        used_tools=used_tools,
    )


# ─── 主入口 ──────────────────────────────────────────────


async def run_agent(
    user_input: str,
    *,
    registry: ToolRegistryProtocol,
    monitor: ToolMonitorProtocol | None = None,
    toolboxes: list[Toolbox] | None = None,
    agent_config: dict[str, Any] | None = None,
    options: AgentRunOptions | None = None,
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
) -> AgentRunResult:
    """运行 Agent（两阶段模式）。

    **核心架构**（Phase 0-2 多阶段智能）：
    - Phase 0: 任务难度分类（simple/normal/medium/complex）
    - Phase 0.5: 需求澄清（三步法：Wittgenstein → Socrates → Polya）
    - Phase 1: 结构化规划（LLM生成 StructuredPlan）
    - Phase 2: ReAct 循环执行（Think → Act → Observe）

    **Phase 0 分类器**（task_classifier_enabled=True）：
    - 简单任务：跳过规划，直接执行（降低延迟和成本）
    - 一般/中等/复杂任务：调用结构化规划器
    - 可通过 agent.skip_task_classification=True 强制关闭

    **Phase 0.5 需求澄清**（clarifier提供时）：
    - Wittgenstein：语言边界检查（是否存在歧义）
    - Socrates：反向追问（通过反例澄清边界）
    - Polya：示例传递（提供具体场景）
    - 澄清轮次最多3轮，避免过度追问

    **Phase 1 规划**（skip_planning=False 且有toolboxes）：
    - LLM分析需求，生成 StructuredPlan（summary、steps、required_toolboxes）
    - 工具箱选择：auto/manual/all 三种策略
    - Token估算：预估上下文消耗，避免超预算
    - 风险评估：高风险操作需用户确认（on_plan回调）

    **Phase 2 执行**（ReAct循环）：
    - 执行器调用 execute_plan，实现 Think → Act → Observe
    - 循环检测：防止无限循环（相似度阈值配置）
    - 记忆上下文分层：结构化会话记忆与关键词检索由执行器放入本轮 user context
    - 流式输出：通过 on_thinking 实时推送思考过程

    **思考展示策略**（on_thinking回调）：
    - EXECUTION_ANNOUNCE_DIFFICULTY=True：合并难度评估和规划展示
    - 分段显示：[评估与计划] → [执行] → [等待确认]
    - 全量记录：full_record参数写入会话历史（飞书/CLI双通道）

    **高风险确认流程**（plan.requires_confirmation=True）：
    - 显示警告提示："⚠️ 高风险操作，请确认执行计划"
    - 调用 on_plan回调等待用户确认
    - 用户可通过 /confirm /reject /adjust 命令响应

    Args:
        user_input: 用户的原始需求文本
        registry: 工具注册表（实现 ToolRegistryProtocol）
        monitor: 性能监控器（默认创建 DefaultToolMonitor）
        toolboxes: 可用工具箱列表（空则跳过规划阶段）
        agent_config: Agent 配置覆盖（如 streaming、debug、max_turns）；优先于 ``options.agent_config``
        options: 运行选项（``system_prompt`` / ``agent_config`` / ``model_config`` 批量注入）
        system_prompt: 自定义系统提示词（覆盖 ``options.system_prompt``）
        skip_planning: 强制跳过规划阶段（直接进入执行）
        on_tool_call: 工具调用回调（用于飞书卡片按钮交互）
        on_tool_finish: 工具执行完成回调（用于历史记录落盘）
        on_plan: 规划确认回调（高风险操作时等待用户响应，返回 :class:`ConfirmationResult`）
        on_thinking: 思考流回调（实时展示思考过程）
        clawhub: ClawHub客户端实例（用于技能市场交互）
        memory_store: 记忆存储实例（默认使用进程bundle）
        activity_log: 活动日志实例（记录会话活动）
        keyword_index: 关键词索引实例（语义检索）
        client: AsyncOpenAI客户端（默认使用进程共享实例）
        clarifier: 需求澄清器实例（实现三步澄清）
        session_key: 会话标识符（用于记忆加载和历史保存）
        confirmation_channel: 确认通道实例（飞书卡片确认）
        engine: UnifiedEngine实例（用于状态访问）

    Returns:
        AgentRunResult: 含 ``reply`` 与工具调用统计
            - 正常完成：``reply`` 为最终回复（可含反思 footer）
            - 操作取消：``reply`` 为 WARNING_PREFIX + "操作已取消"
            - 循环拦截：``reply`` 为 WARNING_PREFIX + 循环提示

    Raises:
        ValueError: 配置参数无效（如 max_turns<1）
        RuntimeError: LLM API调用失败或规划器异常
        ContextBudgetExceeded: 上下文token超预算（由执行器抛出）

    Examples:
        >>> from miniagent.core.agent import run_agent
        >>> from miniagent.infrastructure.registry import DefaultToolRegistry
        >>> registry = DefaultToolRegistry()
        >>> result = await run_agent(
        ...     "帮我分析当前目录的文件结构",
        ...     registry=registry,
        ...     session_key="session_001",
        ... )
        >>> print(result.reply)  # "已分析完成，当前目录包含..."

    Note:
        - Phase 0 分类器默认开启，简单任务自动跳过规划
        - Phase 0.5 澄清需提供 clarifier 实例（见 requirement_clarifier.py）
        - Phase 1 规划器会估算token，避免超 budget
        - Phase 2 执行器最多400轮（见 agent.max_turns）
        - 飞书/CLI双通道通过 engine 统一接线（见 UnifiedEngine）

    See Also:
        - miniagent/core/executor.execute_plan: Phase 2 执行器实现
        - miniagent/core/planner.generate_plan: Phase 1 规划器实现
        - miniagent/core/task_classifier.classify_task_difficulty: Phase 0 分类器
        - miniagent/core/requirement_clarifier.RequirementClarifier: Phase 0.5 澄清器

    Returns:
        AgentRunResult（含最终回复与工具统计）
    """
    if monitor is None:
        monitor = DefaultToolMonitor()
    if toolboxes is None:
        toolboxes = []

    effective_system_prompt = (
        system_prompt if system_prompt is not None else (options.system_prompt if options else None)
    )

    # ── 合并配置（options 先合并，agent_config 覆盖）──
    base_config = get_default_agent_config()
    options_overlay: dict[str, Any] = {}
    if options is not None and options.agent_config:
        options_overlay.update(options.agent_config)
    if options is not None and options.model_config:
        model_overrides = dict(options_overlay.get("model_overrides") or {})
        model_overrides.update(options.model_config)
        options_overlay["model_overrides"] = model_overrides
    if options_overlay:
        base_config = merge_agent_config(base_config, options_overlay)
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
            if result.rejected:
                return ""
            answer = (result.adjustment or "").strip()
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
        plan_input = user_input
        replan_attempts_left = max(1, int(get_config("agent.max_plan_confirm_rounds", 3)))

        while True:
            plan = await generate_plan(
                plan_input,
                toolboxes,
                merged_config.log_file,
                client=client,
                agent_config=merged_config,
                registry=registry,
                planner_model_overrides=planner_merge_for_difficulty(difficulty),
                default_step_thinking=default_step_thinking_for_difficulty(difficulty),
            )
            merged_config = _merge_plan_suggested_config(plan, merged_config)

            if merged_config.debug:
                _logger.info("规划结果: %s", plan.summary)
                _logger.debug("工具箱: %s", ", ".join(plan.required_toolboxes))
                _logger.debug("预估 token: %d", plan.estimated_tokens.total)
                _logger.debug("风险等级: %s", plan.risk_level)

            if not plan.requires_confirmation or not on_plan:
                break

            if on_thinking:
                try:
                    await invoke_on_thinking(
                        on_thinking,
                        f"{WARNING_PREFIX} 高风险操作，请确认执行计划。输入 /confirm 同意，/reject 拒绝，/adjust 调整。",
                        True,
                        "[等待确认]",
                    )
                except Exception as e:
                    _logger.debug("等待确认推送失败（非关键）: %s", e)

            result = await on_plan(plan)
            action, adjustment = result.plan_action()
            if action == "cancel":
                return _build_agent_run_result(f"{WARNING_PREFIX} 操作已取消", monitor)
            if action == "replan":
                replan_attempts_left -= 1
                if replan_attempts_left <= 0:
                    return _build_agent_run_result(
                        f"{WARNING_PREFIX} 计划调整次数过多，已取消",
                        monitor,
                    )
                if adjustment:
                    plan_input = f"{plan_input}\n\n[用户计划调整] {adjustment}"
                continue
            break

    if _announce_difficulty_and_plan_enabled() and on_thinking:
        no_toolboxes_flag = len(toolboxes) == 0
        simple_classified_flag = (
            bool(toolboxes) and not skip_planning and difficulty == TaskDifficulty.SIMPLE
        )
        plan_msg = _format_plan_message(
            plan,
            from_llm_planner=from_llm_planner,
            no_toolboxes=no_toolboxes_flag,
            user_skip_planning=skip_planning,
            simple_classified=simple_classified_flag,
        )
        plan_disp = _format_plan_display_short(
            plan,
            from_llm_planner=from_llm_planner,
            no_toolboxes=no_toolboxes_flag,
            user_skip_planning=skip_planning,
            simple_classified=simple_classified_flag,
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
        system_prompt=effective_system_prompt,
        clawhub=clawhub,
        memory_store=memory_store,
        activity_log=activity_log,
        keyword_index=keyword_index,
        client=client,
    )

    # ── Phase 3: 反思评估 ──
    reflection_enabled = get_config("features.reflection", True)
    if reflection_enabled:
        reflection = await reflect_on_result(
            user_input, reply, client=client, on_thinking=None, session_key=session_key
        )
        # 存储到引擎供飞书发送独立质量卡片
        if engine is not None:
            sk = session_key or "default"
            if not hasattr(engine, "_last_reflection") or not isinstance(engine._last_reflection, dict):
                engine._last_reflection = {}
            engine._last_reflection[sk] = reflection
        # 展示层：在回复末尾追加质量评估尾部（CLI 终端 / 飞书卡片）。
        # 注意：footer 仅用于展示，落入会话历史后由 history_bridge 在回灌 LLM 前
        # 剥离（见 strip_reflection_footer），避免下一轮被模型复述导致重复评估。
        reply = reply + build_reflection_footer(reflection)

    return _build_agent_run_result(reply, monitor)


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
    results: list[PipelineStepRecord] = []
    pipeline_content = ""
    pipeline_success = True

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
            err_result: ToolCallResult = {
                "success": False,
                "content": f"{WARNING_PREFIX} 未知工具: {step.tool}",
            }
            results.append({"tool": step.tool, "args": step.args, "result": err_result})
            return PipelineResult(
                steps=results,
                final_content=err_result["content"],
                success=False,
            )

        result = await tool.handler(step.args, context)
        step_record: PipelineStepRecord = {
            "tool": step.tool,
            "args": step.args,
            "result": {"success": result.success, "content": result.content},
        }
        results.append(step_record)
        pipeline_content += result.content + "\n"

        if on_tool_call:
            on_tool_call(step.tool, json.dumps(step.args), result.content)

        if not result.success:
            pipeline_success = False
            break

    return PipelineResult(
        steps=results,
        final_content=pipeline_content.strip(),
        success=pipeline_success,
    )


# ─── 内部辅助 ────────────────────────────────────────────


def _create_default_plan() -> StructuredPlan:
    """创建默认计划（直接执行模式）。

    用于以下场景：
    - 无可用工具箱（纯对话模式）
    - 用户显式跳过规划（skip_planning=True）
    - 简单任务自动跳过规划（TaskDifficulty.SIMPLE）

    Returns:
        StructuredPlan: 包含默认配置的结构化计划
            - summary: "直接执行模式"
            - steps: 空列表（无分步执行）
            - required_toolboxes: 空列表
            - risk_level: "low"
            - max_turns: None（使用全局默认值）

    Note:
        该计划会被传递给执行器，触发单阶段 ReAct 循环（非分步模式）。
    """
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
