"""Adaptive Policy Engine — 根据控制状态自动调整执行策略。

本模块将固定策略的 ReAct 循环升级为**自适应控制系统**，
根据 :class:`feedback_controller.ControlState` 和 :class:`state_observer.AgentState`
动态选择执行策略。

设计哲学（基于控制论的自适应概念）：
- **自适应 (Adaptive Control)**：系统参数变化时自动调整控制律
- **状态→动作映射**：稳定→正常、震荡→简化、发散→终止、停滞→重规划
- **渐进升级**：发散/停滞不会立即触发极端动作，而是累计计数后 escalate

策略映射表：
====== ============ =================================================
状态   动作          说明
====== ============ =================================================
STABLE NORMAL        正常执行，不变
OSCILLATING SIMPLIFY 减少工具数、降低 thinking_budget
DIVERGING SIMPLIFY→TERMINATE 简化→持续发散则终止
STUCK   SIMPLIFY→REPLAN 简化→持续停滞则重规划
CONVERGED CONVERGED_EXIT 误差已收敛，提前退出
HIGH_CTX COMPRESS    上下文使用率超阈值，触发压缩
====== ============ =================================================

详见 ``docs/CYBERNETICS_PLAN.md`` Phase 3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from miniagent.core.feedback_controller import ControlState
from miniagent.core.state_observer import AgentState


class AdaptiveAction(str, Enum):
    """自适应动作枚举。"""

    NORMAL = "normal"           # 正常执行
    SIMPLIFY = "simplify"       # 简化问题，减少工具调用
    TERMINATE = "terminate"     # 提前终止，返回最佳部分结果
    REPLAN = "replan"           # 重新规划
    COMPRESS = "compress"       # 压缩上下文
    CONVERGED_EXIT = "converged_exit"  # 已收敛，正常退出


@dataclass
class AdaptiveDecision:
    """自适应决策结果。"""

    action: AdaptiveAction
    reason: str
    config_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdaptivePolicy:
    """根据 ControlState + AgentState 选择策略。

    Args:
        max_context_ratio: 上下文使用率超过此值时触发 COMPRESS（默认 0.85）
        max_turns_before_replan: 超过此轮次仍在 STUCK 时触发 REPLAN（默认 10）
        max_diverge_turns: DIVERGING 持续此轮次后触发 TERMINATE（默认 5）
    """

    max_context_ratio: float = 0.85
    max_turns_before_replan: int = 10
    max_diverge_turns: int = 5

    _stuck_turns: int = field(default=0, repr=False, init=False)
    _diverge_turns: int = field(default=0, repr=False, init=False)

    def decide(
        self,
        control_state: ControlState,
        agent_state: AgentState | None = None,
    ) -> AdaptiveDecision:
        """根据当前状态做出自适应决策。

        Args:
            control_state: 反馈控制器的当前状态
            agent_state: Agent 的当前状态向量（可选）

        Returns:
            自适应决策
        """
        # 先检查上下文使用率（优先级高）
        if agent_state and agent_state.context_usage_ratio > self.max_context_ratio:
            return AdaptiveDecision(
                action=AdaptiveAction.COMPRESS,
                reason=f"上下文使用率 {agent_state.context_usage_ratio:.0%} 超过阈值 {self.max_context_ratio:.0%}",
                config_overrides={"needs_compression": True},
            )

        # 收敛 → 提前退出
        if control_state == ControlState.CONVERGED:
            return AdaptiveDecision(
                action=AdaptiveAction.CONVERGED_EXIT,
                reason="误差已收敛到阈值以下，可结束执行",
            )

        # 发散 → 累计计数，超过阈值则终止
        if control_state == ControlState.DIVERGING:
            self._diverge_turns += 1
            self._stuck_turns = 0  # 重置 stuck 计数
            if self._diverge_turns >= self.max_diverge_turns:
                return AdaptiveDecision(
                    action=AdaptiveAction.TERMINATE,
                    reason=f"发散状态持续 {self._diverge_turns} 轮，终止执行",
                )
            return AdaptiveDecision(
                action=AdaptiveAction.SIMPLIFY,
                reason=f"检测到发散趋势（{self._diverge_turns}/{self.max_diverge_turns} 轮），简化策略",
                config_overrides={"reduce_tools": True},
            )

        # 震荡 → 简化
        if control_state == ControlState.OSCILLATING:
            self._stuck_turns = 0
            self._diverge_turns = 0
            return AdaptiveDecision(
                action=AdaptiveAction.SIMPLIFY,
                reason="误差呈震荡趋势，简化问题或减少工具调用",
                config_overrides={"reduce_tools": True, "lower_thinking_budget": True},
            )

        # 停滞 → 累计计数，超过阈值则重规划
        if control_state == ControlState.STUCK:
            self._stuck_turns += 1
            self._diverge_turns = 0
            if self._stuck_turns >= self.max_turns_before_replan:
                return AdaptiveDecision(
                    action=AdaptiveAction.REPLAN,
                    reason=f"停滞 {self._stuck_turns} 轮，需要重新规划",
                )
            return AdaptiveDecision(
                action=AdaptiveAction.SIMPLIFY,
                reason=f"执行停滞（{self._stuck_turns}/{self.max_turns_before_replan} 轮），尝试简化",
                config_overrides={"reduce_tools": True},
            )

        # 稳定 → 正常执行
        self._stuck_turns = 0
        self._diverge_turns = 0
        return AdaptiveDecision(
            action=AdaptiveAction.NORMAL,
            reason="状态稳定，继续正常执行",
        )

    def reset(self) -> None:
        """重置策略器内部计数（新会话或重规划时调用）。"""
        self._stuck_turns = 0
        self._diverge_turns = 0


__all__ = ["AdaptiveAction", "AdaptiveDecision", "AdaptivePolicy"]
