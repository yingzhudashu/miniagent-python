"""规划类型运行时辅助：步骤排序、分块分组、上下文策略映射。

本模块在 :mod:`miniagent.agent.types.planning` 数据契约与 Phase 2 执行之间做轻量变换；
LLM JSON 的完整解析流程见 :func:`miniagent.agent.planner._dict_to_plan`（含步骤规范化）。

职责划分：
- :func:`order_steps_by_dependencies` — 单组步骤拓扑排序
- :func:`resolve_execution_step_groups` — 按 chunk 或整表分组（供 Executor 分步执行）
- :func:`resolve_effective_overflow_strategy` / :func:`resolve_chunk_compress_threshold` — 上下文策略
- :func:`format_output_spec_block` / :func:`format_estimated_cost_block` — 计划展示与 prompt 注入
- :func:`parse_plan_steps_from_raw` / :func:`parse_plan_chunks_from_raw` — 从 camelCase JSON 解析步骤
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from miniagent.agent.types.planning import (
    EstimatedCost,
    OutputSpec,
    PlanChunk,
    PlanStep,
    StructuredPlan,
    ThinkingLevel,
)

StepAsDict = Callable[[object, int], dict[str, Any]]
StepThinkingLevel = Callable[[dict[str, Any]], ThinkingLevel | None]


def _resolve_step_depends_on(dep: object, by_num: dict[int, PlanStep]) -> int | None:
    """将 ``depends_on`` 规范为可匹配的步骤序号；无效或缺失依赖返回 ``None``。"""
    if dep is None:
        return None
    if not isinstance(dep, (str, bytes, bytearray, int, float)):
        return None
    try:
        n = int(dep)
    except (TypeError, ValueError):
        return None
    return n if n in by_num else None


def order_steps_by_dependencies(steps: list[PlanStep]) -> list[PlanStep]:
    """按 ``depends_on`` 拓扑排序步骤。

    同一 ``step_number`` 出现多次时保留全部步骤（依赖解析以**首次**出现的编号为准）。
    检测到环时不再沿依赖深入，按输入列表顺序兜底追加未访问步骤。

    Args:
        steps: 待排序步骤（可为乱序）

    Returns:
        依赖优先的步骤列表；空输入返回空列表。
    """
    if not steps:
        return []
    if len(steps) == 1:
        return list(steps)

    indexed = list(steps)
    by_num: dict[int, PlanStep] = {}
    for step in indexed:
        if step.step_number not in by_num:
            by_num[step.step_number] = step

    ordered: list[PlanStep] = []
    visited_ids: set[int] = set()
    visiting_nums: set[int] = set()

    def visit(step: PlanStep) -> None:
        sid = id(step)
        if sid in visited_ids:
            return
        sn = step.step_number
        if sn in visiting_nums:
            visited_ids.add(sid)
            ordered.append(step)
            return
        visiting_nums.add(sn)
        dep_num = _resolve_step_depends_on(step.depends_on, by_num)
        if dep_num is not None:
            visit(by_num[dep_num])
        visiting_nums.discard(sn)
        if sid in visited_ids:
            return
        visited_ids.add(sid)
        ordered.append(step)

    for step in indexed:
        visit(step)

    for step in indexed:
        if id(step) not in visited_ids:
            ordered.append(step)
            visited_ids.add(id(step))
    return ordered


def resolve_execution_step_groups(plan: StructuredPlan) -> list[tuple[str, list[PlanStep]]]:
    """解析 Phase 2 步骤分组。

    优先使用 ``context_strategy.chunks``：按 ``chunk_number`` 排序，块内拓扑排序。
    若无有效 chunk，则将 ``plan.steps`` 整体作为**单一分组**（非「每步一组」）。
    存在 chunks 时忽略扁平 ``plan.steps``；跨 chunk 的 ``depends_on`` 仅在块内排序。

    Args:
        plan: Phase 1 结构化计划

    Returns:
        ``(chunk_system_prompt, ordered_steps)`` 列表；无步骤时为空列表。
    """
    chunks = plan.context_strategy.chunks if plan.context_strategy else None
    if chunks:
        groups: list[tuple[str, list[PlanStep]]] = []
        for chunk in sorted(chunks, key=lambda c: c.chunk_number):
            ordered = order_steps_by_dependencies(chunk.steps)
            if ordered:
                groups.append((chunk.chunk_system_prompt or "", ordered))
        if groups:
            return groups

    ordered_steps = order_steps_by_dependencies(plan.steps)
    if not ordered_steps:
        return []
    return [("", ordered_steps)]


def resolve_effective_overflow_strategy(plan: StructuredPlan, default: str) -> str:
    """合并规划建议与 ``context_strategy.mode``，得到上下文溢出策略。

    优先级：``suggested_config.context_overflow_strategy``（非空）
    → ``mode`` 为 ``summarize`` / ``truncate``
    → ``default``（通常为 Agent 配置）。

    ``chunked``、``normal`` 等模式不映射溢出策略，回退 ``default``。

    Args:
        plan: 结构化计划
        default: Agent 默认溢出策略（如 ``error``、``truncate``）

    Returns:
        生效的溢出策略字符串。
    """
    sc = plan.suggested_config
    if sc and sc.context_overflow_strategy:
        return sc.context_overflow_strategy
    raw_mode = (plan.context_strategy.mode if plan.context_strategy else "normal") or "normal"
    normalized_mode = str(raw_mode).lower()
    if normalized_mode == "summarize":
        return "summarize"
    if normalized_mode == "truncate":
        return "truncate"
    return default


def resolve_chunk_compress_threshold(
    plan: StructuredPlan,
    *,
    context_window: int,
    default_threshold: float,
) -> float:
    """根据 ``chunk_token_budget`` 收紧压缩阈值（预算越小越早压缩）。

    ``ratio = clamp(budget / context_window, 0.25, 0.95)``，
    返回 ``min(default_threshold, ratio)``，不会高于 Agent 默认阈值。

    Args:
        plan: 结构化计划
        context_window: 模型上下文窗口（token）
        default_threshold: Agent 默认压缩阈值

    Returns:
        生效的压缩阈值（0–1）。
    """
    budget = plan.suggested_config.chunk_token_budget if plan.suggested_config else None
    if not budget or context_window <= 0:
        return default_threshold
    ratio = min(0.95, max(0.25, budget / context_window))
    return min(default_threshold, ratio)


def format_output_spec_block(spec: OutputSpec) -> str | None:
    """将输出规格格式化为可注入 user context 的文本块。

    全部为默认值（``zh-CN``、``markdown``、无交付物）时返回 ``None``。
    仅非默认字段写入文本（例如仅有交付物时不重复默认语言/格式）。

    Args:
        spec: 计划输出规格

    Returns:
        格式化文本，或无需注入时 ``None``。
    """
    deliverable = (spec.expected_deliverable or "").strip()
    lang = (spec.language or "").strip()
    fmt = (spec.format or "").strip()
    has_non_default = bool(deliverable) or lang not in ("", "zh-CN") or fmt not in ("", "markdown")
    if not has_non_default:
        return None
    parts: list[str] = []
    if lang and lang not in ("", "zh-CN"):
        parts.append(f"语言：{lang}")
    if fmt and fmt not in ("", "markdown"):
        parts.append(f"格式：{fmt}")
    if deliverable:
        parts.append(f"交付物：{deliverable}")
    if not parts:
        return None
    return "输出规格：\n" + "\n".join(parts)


def format_estimated_cost_block(cost: EstimatedCost) -> str | None:
    """将成本预估格式化为计划展示文本。

    Args:
        cost: 成本预估

    Returns:
        展示用文本；各项均为零时 ``None``。
    """
    if cost.total_usd <= 0 and cost.input_tokens <= 0 and cost.output_tokens <= 0:
        return None
    lines = [
        f"输入 token 约 {cost.input_tokens}",
        f"输出 token 约 {cost.output_tokens}",
    ]
    if cost.total_usd > 0:
        lines.append(f"预估成本约 ${cost.total_usd:.4f}")
    return "成本预估：\n" + "\n".join(lines)


def _parse_depends_on(raw: object) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, (str, bytes, bytearray, int, float)):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_plan_steps_from_raw(
    raw_steps: list[object],
    *,
    step_as_dict: StepAsDict,
    step_thinking_level: StepThinkingLevel,
) -> list[PlanStep]:
    """从 LLM JSON 步骤数组解析 ``PlanStep`` 列表。

    ``step_as_dict`` / ``step_thinking_level`` 由 :mod:`miniagent.agent.planner` 注入，
    负责 str/dict 归一化与 thinking 档位回落。

    Args:
        raw_steps: ``steps`` 或 chunk 内 ``steps`` 原始数组
        step_as_dict: ``(raw_item, index) ->`` 字段字典
        step_thinking_level: 从步骤字典解析 ``thinking_level``

    Returns:
        解析后的步骤列表（未经 :func:`miniagent.agent.planner._normalize_plan_steps` 规范化）。
    """
    steps: list[PlanStep] = []
    for i, raw in enumerate(raw_steps, start=1):
        s = step_as_dict(raw, i)
        steps.append(
            PlanStep(
                step_number=int(s.get("stepNumber", 0) or 0),
                description=str(s.get("description", "") or ""),
                required_toolboxes=list(s.get("requiredToolboxes") or []),
                expected_input=str(s.get("expectedInput", "") or ""),
                expected_output=str(s.get("expectedOutput", "") or ""),
                depends_on=_parse_depends_on(s.get("dependsOn")),
                thinking_level=step_thinking_level(s),
            )
        )
    return steps


def parse_plan_chunks_from_raw(
    raw_chunks: list[object],
    *,
    step_as_dict: StepAsDict,
    step_thinking_level: StepThinkingLevel,
) -> list[PlanChunk] | None:
    """从 ``contextStrategy.chunks`` 解析分块列表。

    非 dict 项跳过；全部无效或为空时返回 ``None``。

    Args:
        raw_chunks: ``contextStrategy.chunks`` 原始数组
        step_as_dict: 步骤归一化回调（同 :func:`parse_plan_steps_from_raw`）
        step_thinking_level: thinking 档位回调

    Returns:
        分块列表，或 ``None``。
    """
    if not raw_chunks:
        return None
    chunks: list[PlanChunk] = []
    for i, item in enumerate(raw_chunks, start=1):
        if not isinstance(item, dict):
            continue
        raw_steps = item.get("steps", [])
        if not isinstance(raw_steps, list):
            raw_steps = []
        chunk_steps = parse_plan_steps_from_raw(
            raw_steps,
            step_as_dict=step_as_dict,
            step_thinking_level=step_thinking_level,
        )
        chunks.append(
            PlanChunk(
                chunk_number=int(item.get("chunkNumber", i) or i),
                steps=chunk_steps,
                estimated_tokens=int(item.get("estimatedTokens", 0) or 0),
                chunk_system_prompt=str(item.get("chunkSystemPrompt", "") or ""),
            )
        )
    return chunks or None


__all__ = [
    "format_estimated_cost_block",
    "format_output_spec_block",
    "order_steps_by_dependencies",
    "parse_plan_chunks_from_raw",
    "parse_plan_steps_from_raw",
    "resolve_chunk_compress_threshold",
    "resolve_effective_overflow_strategy",
    "resolve_execution_step_groups",
    "StepAsDict",
    "StepThinkingLevel",
]
