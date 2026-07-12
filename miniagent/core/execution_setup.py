"""执行计划的工具筛选与步骤展示纯函数。"""

from __future__ import annotations

from typing import Any

from miniagent.types.config import AgentConfig
from miniagent.types.planning import PlanStep, StructuredPlan
from miniagent.types.tool import ToolRegistryProtocol


def _resolve_exec_tools(
    effective_registry: ToolRegistryProtocol,
    agent_config: AgentConfig,
    plan: StructuredPlan,
    step: PlanStep | None,
) -> list[Any]:
    """根据工具选择策略筛选本轮可用工具定义列表。

    工具筛选遵循三级优先级：
    1. 步骤级工具箱（step.required_toolboxes）：分步执行时按步骤覆盖
    2. 计划级工具箱（plan.required_toolboxes）：规划器指定的工具箱
    3. 策略回退（tool_selection_strategy）：all/auto/manual 三种模式

    **策略说明**：
    - "all": 所有已注册工具（无筛选）
    - "auto": 有工具箱时按工具箱筛选，否则返回核心工具（toolbox=None）
    - "manual": 严格按工具箱筛选（plan/step 指定的工具箱）

    Args:
        effective_registry: 工具注册表（可能是会话级覆盖注册表）
        agent_config: Agent 配置（含 tool_selection_strategy）
        plan: 结构化执行计划
        step: 当前执行步骤（None 表示非分步模式）

    Returns:
        list[Any]: 工具定义 schema 列表（传递给 LLM tools 参数）

    Note:
        分步模式下每步可使用不同工具集，提升安全性和 token 效率。
    """
    if not plan.tools_enabled:
        return []
    step_tbs = list(step.required_toolboxes) if step and step.required_toolboxes else None
    plan_tbs = plan.required_toolboxes

    if agent_config.tool_selection_strategy == "all":
        return effective_registry.get_schemas()
    if agent_config.tool_selection_strategy == "auto":
        tbs = step_tbs if step_tbs else plan_tbs
        if tbs:
            return effective_registry.get_schemas_by_toolboxes(tbs)
        tools = [t.schema for t in effective_registry.get_all().values() if t.toolbox is None]
        return tools if tools else effective_registry.get_schemas()
    tbs = step_tbs if step_tbs else plan_tbs
    return effective_registry.get_schemas_by_toolboxes(tbs)


def _step_thinking_header(si: int, n_steps: int, step: PlanStep) -> str:
    """生成分步执行时的步骤级思考展示 header。

    用于分步模式（PHASED_EXECUTION）的思考流分段标题，格式：
    "[步骤 {step_number}/{total_steps}] {description}"

    Args:
        si: 步骤索引（从 0 开始）
        n_steps: 总步骤数
        step: 当前步骤对象

    Returns:
        str: 步骤 header 文本（用于 on_thinking 的 header 参数）

    Note:
        描述超过 72 字符时自动截断并添加省略号。
    """
    sn = int(step.step_number) if step.step_number is not None else si + 1
    desc = (step.description or "").strip().replace("\n", " ")
    if len(desc) > 72:
        desc = desc[:69] + "…"
    return f"[步骤 {sn}/{n_steps}] {desc}".strip()


__all__ = ["_resolve_exec_tools", "_step_thinking_header"]

