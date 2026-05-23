"""State Observer — Agent 执行状态的可观测性。

本模块提供 **AgentState** 状态向量和 **StateObserver** 观测器，
每轮 ReAct 执行后更新状态，供日志、调试和自适应策略使用。

设计哲学（基于控制论的可观测性概念）：
- **可观测性 (Observability)**：从工具调用结果推断 Agent 内部执行状态
- **状态向量**：context_usage_ratio, tool_success_rate, convergence_velocity 等
- **时间窗口**：最近 N 轮滑动窗口，避免历史噪音干扰当前判断

使用方式：
    >>> from miniagent.core.state_observer import StateObserver
    >>> obs = StateObserver(recent_window=5)
    >>> obs.record_tool("web_search", success=True)
    >>> obs.record_tool("read_file", success=False)
    >>> state = obs.end_turn()
    >>> print(state.tool_success_rate)  # 0.5
    >>> print(obs.readable_state(state))

与 :mod:`feedback_controller` 的集成：
``end_turn()`` 接受 controller 参数，自动读取最新误差值写入 ``convergence_velocity``。

详见 ``docs/CYBERNETICS_PLAN.md`` Phase 2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.core.feedback_controller import FeedbackController
    from miniagent.memory.context import DefaultContextManager


@dataclass
class AgentState:
    """Agent 状态向量。

    每轮 ReAct 执行后由 :class:`StateObserver` 更新。
    """

    context_usage_ratio: float = 0.0       # 上下文窗口使用率 (0-1)
    tool_success_rate: float = 1.0         # 最近 N 轮工具成功率 (0-1)
    convergence_velocity: float = 0.0      # 误差变化速度（来自反馈控制器）
    unique_tool_call_ratio: float = 1.0    # 工具调用去重率 (0-1)
    token_budget_remaining: int = 0        # 剩余 token 预算
    total_tool_calls: int = 0              # 累计工具调用次数
    total_turns: int = 0                   # 累计 ReAct 轮次

    def to_dict(self) -> dict[str, Any]:
        """转为字典（供日志/调试）。"""
        return {
            "context_usage_ratio": round(self.context_usage_ratio, 3),
            "tool_success_rate": round(self.tool_success_rate, 3),
            "convergence_velocity": round(self.convergence_velocity, 4),
            "unique_tool_call_ratio": round(self.unique_tool_call_ratio, 3),
            "token_budget_remaining": self.token_budget_remaining,
            "total_tool_calls": self.total_tool_calls,
            "total_turns": self.total_turns,
        }


@dataclass
class StateObserver:
    """状态观测器：跟踪并聚合 Agent 执行状态。

    Args:
        recent_window: 计算"最近 N 轮"统计的窗口大小（默认 5）
    """

    recent_window: int = 5

    _turn_successes: list[int] = field(default_factory=list, repr=False, init=False)
    _turn_tool_calls: list[set[str]] = field(default_factory=list, repr=False, init=False)
    _turn_call_counts: list[int] = field(default_factory=list, repr=False, init=False)
    _current_turn_success: int = field(default=0, repr=False, init=False)
    _current_turn_fail: int = field(default=0, repr=False, init=False)
    _current_turn_tools: set[str] = field(default_factory=set, repr=False, init=False)
    _current_turn_calls: int = field(default=0, repr=False, init=False)
    _total_tools: int = field(default=0, repr=False, init=False)
    _total_success: int = field(default=0, repr=False, init=False)
    _total_calls: int = field(default=0, repr=False, init=False)
    _turn_count: int = field(default=0, repr=False, init=False)

    def record_tool(self, tool_name: str, success: bool) -> None:
        """记录本轮内的一个工具调用结果。

        在 :meth:`end_turn` 之前调用，累积到当前轮。
        """
        self._current_turn_tools.add(tool_name)
        self._current_turn_calls += 1
        self._total_calls += 1
        if success:
            self._current_turn_success += 1
            self._total_success += 1
        else:
            self._current_turn_fail += 1

    def end_turn(
        self,
        context_manager: DefaultContextManager | None = None,
        controller: FeedbackController | None = None,
    ) -> AgentState:
        """结束当前轮，计算状态向量。

        Args:
            context_manager: 上下文管理器（用于获取 token 用量）
            controller: 反馈控制器（用于获取收敛速度）
        """
        self._turn_count += 1

        # 本轮统计写入历史
        turn_total = self._current_turn_success + self._current_turn_fail
        if turn_total > 0:
            self._turn_successes.append(self._current_turn_success)
            self._turn_tool_calls.append(set(self._current_turn_tools))
            self._turn_call_counts.append(self._current_turn_calls)
        self._total_tools += len(self._current_turn_tools)

        # 最近 N 轮工具成功率
        recent_successes = self._turn_successes[-self.recent_window:]
        recent_counts = self._turn_call_counts[-self.recent_window:]
        if recent_successes:
            recent_ok = sum(recent_successes)
            recent_total = sum(recent_counts) if recent_counts else 0
            tool_success_rate = recent_ok / recent_total if recent_total > 0 else 1.0
        else:
            tool_success_rate = 1.0

        # 工具调用去重率（最近 N 轮）
        recent_tools_list = self._turn_tool_calls[-self.recent_window:]
        recent_call_counts = self._turn_call_counts[-self.recent_window:]
        if recent_tools_list:
            unique_tools = set()
            for s in recent_tools_list:
                unique_tools.update(s)
            total_in_window = sum(recent_call_counts) if recent_call_counts else 0
            unique_ratio = len(unique_tools) / total_in_window if total_in_window > 0 else 1.0
        else:
            unique_ratio = 1.0

        # 上下文窗口使用率
        context_ratio = 0.0
        token_budget = 0
        if context_manager is not None:
            total = getattr(context_manager, "_total_tokens_estimate", 0)
            window = getattr(context_manager, "_context_window", 128000)
            context_ratio = total / window if window > 0 else 0.0
            # 粗略估算剩余 token
            token_budget = max(0, window - total - int(window * 0.1))

        # 收敛速度（来自反馈控制器）
        convergence_vel = 0.0
        if controller is not None:
            vel = controller.get_latest_error()
            convergence_vel = vel if vel is not None else 0.0

        state = AgentState(
            context_usage_ratio=round(context_ratio, 4),
            tool_success_rate=round(tool_success_rate, 4),
            convergence_velocity=round(convergence_vel, 4),
            unique_tool_call_ratio=round(unique_ratio, 4),
            token_budget_remaining=token_budget,
            total_tool_calls=self._total_calls,
            total_turns=self._turn_count,
        )

        # 重置当前轮缓存
        self._current_turn_success = 0
        self._current_turn_fail = 0
        self._current_turn_tools.clear()
        self._current_turn_calls = 0

        return state

    def readable_state(self, state: AgentState | None = None) -> str:
        """生成人类可读的状态报告。

        Args:
            state: 最近一次的状态；None 时生成空壳报告
        """
        if state is None:
            state = AgentState()

        lines = [
            f"  轮次: {state.total_turns} | 工具调用: {state.total_tool_calls}",
            f"  上下文使用: {state.context_usage_ratio:.0%}",
            f"  工具成功率: {state.tool_success_rate:.0%}",
            f"  工具去重率: {state.unique_tool_call_ratio:.0%}",
            f"  Token 剩余: {state.token_budget_remaining}",
        ]
        if state.convergence_velocity > 0:
            lines.append(f"  当前误差: {state.convergence_velocity:.3f}")
        return "\n".join(lines)

    def reset(self) -> None:
        """重置观测器（新会话或重规划时调用）。"""
        self._turn_successes.clear()
        self._turn_tool_calls.clear()
        self._turn_call_counts.clear()
        self._current_turn_success = 0
        self._current_turn_fail = 0
        self._current_turn_tools.clear()
        self._current_turn_calls = 0
        self._total_tools = 0
        self._total_success = 0
        self._total_calls = 0
        self._turn_count = 0


__all__ = ["AgentState", "StateObserver"]
