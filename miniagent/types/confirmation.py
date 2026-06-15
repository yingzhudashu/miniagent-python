"""Confirmation types — 交互式澄清/规划的确认机制。

Agent 在三类场景通过 :class:`miniagent.core.confirmation_channel.ConfirmationChannel`
暂停执行并等待用户响应：

- **CLARIFICATION**：需求澄清追问（用户直接回复文本）
- **PLAN**：高风险计划确认（``/confirm`` / ``/reject`` / ``/adjust`` 或飞书按钮）
- **TOOL**：``ToolDefinition.permission=require-confirm`` 的工具执行前确认（``/confirm`` / ``/reject``）

典型流程::

    req = ConfirmationRequest(stage=ConfirmationStage.PLAN, content=summary, ...)
    result = await channel.request_confirmation(req)
    action, adjustment = result.plan_action()
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal


class ConfirmationStage(enum.Enum):
    """确认所处的阶段。"""

    CLARIFICATION = "clarification"  # 需求澄清过程中的交互追问
    PLAN = "plan"  # 高风险计划生成后的确认
    TOOL = "tool"  # require-confirm 工具执行前的确认


PlanConfirmationAction = Literal["proceed", "cancel", "replan"]


@dataclass
class ConfirmationRequest:
    """待确认的请求。

    Attributes:
        stage: 当前所处阶段（澄清、计划或工具确认）
        content: 展示给用户的简短内容（追问 / 计划摘要）
        full_content: 完整内容；计划阶段为完整计划文本，供 ``/adjust`` 无参数时参考
        context: 额外上下文；计划阶段可含 ``plan_summary``、``risk_level`` 等元数据
    """

    stage: ConfirmationStage
    content: str
    full_content: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfirmationResult:
    """用户的确认结果。

    Attributes:
        approved: 是否批准继续（计划阶段 ``/confirm`` 或 ``/adjust`` 均为 True）
        adjustment: 用户补充或调整文本
        rejected: 是否明确拒绝（计划阶段 ``/reject`` 时为 True）

    计划阶段语义（见 :meth:`plan_action`）：

    - ``approved=True`` 且无 ``adjustment`` → 直接执行
    - ``approved=True`` 且带 ``adjustment`` → 按调整重新规划
    - ``rejected=True`` → 取消
    - ``approved=False``、``rejected=False`` 且带 ``adjustment`` → 按调整重新规划
    """

    approved: bool
    adjustment: str | None = None
    rejected: bool = False

    def __post_init__(self) -> None:
        if self.rejected:
            object.__setattr__(self, "approved", False)

    @classmethod
    def confirm(cls) -> ConfirmationResult:
        """用户确认继续。"""
        return cls(approved=True)

    @classmethod
    def reject(cls) -> ConfirmationResult:
        """用户明确拒绝。"""
        return cls(approved=False, rejected=True)

    @classmethod
    def adjust(cls, text: str, *, approve: bool = True) -> ConfirmationResult:
        """用户提交调整文本；默认同时视为确认。"""
        return cls(approved=approve, adjustment=text.strip() or None)

    @classmethod
    def clarification_reply(cls, text: str) -> ConfirmationResult:
        """澄清阶段的自由文本回复。"""
        return cls(approved=True, adjustment=(text or "").strip() or None)

    def plan_action(self) -> tuple[PlanConfirmationAction, str | None]:
        """解析计划确认结果，供 Agent 决定继续、取消或重规划。"""
        if self.rejected:
            return "cancel", None

        adjustment = (self.adjustment or "").strip() or None
        if not self.approved:
            if adjustment:
                return "replan", adjustment
            return "cancel", None
        if adjustment:
            return "replan", adjustment
        return "proceed", None


__all__ = [
    "ConfirmationStage",
    "ConfirmationRequest",
    "ConfirmationResult",
    "PlanConfirmationAction",
]
