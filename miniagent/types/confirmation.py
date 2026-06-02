"""Confirmation types — 交互式澄清/规划的确认机制。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ConfirmationStage(enum.Enum):
    """确认所处的阶段。"""

    CLARIFICATION = "clarification"  # 需求澄清后确认
    PLAN = "plan"  # 规划后确认


@dataclass
class ConfirmationRequest:
    """待确认的请求。

    Attributes:
        stage: 当前所处阶段
        content: 展示给用户的简短内容
        full_content: 完整内容（用于调整时参考）
        context: 额外上下文（如 plan 对象序列化）
    """

    stage: ConfirmationStage
    content: str
    full_content: str = ""
    context: dict = field(default_factory=dict)


@dataclass
class ConfirmationResult:
    """用户的确认结果。

    Attributes:
        approved: 是否批准
        adjustment: 用户的调整文本（approved=True 时可同时提供）
        rejected: 是否拒绝（approved=False 且 non-empty adjustment 时视为调整而非取消）
    """

    approved: bool
    adjustment: str | None = None
    rejected: bool = False


__all__ = ["ConfirmationStage", "ConfirmationRequest", "ConfirmationResult"]
