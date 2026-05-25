"""Feedback Controller — ReAct 循环的闭环控制。

本模块将经验式的 ReAct 循环（Think → Act → Observe）升级为**受控反馈系统**，
每轮计算误差信号、稳定性指数和收敛趋势，供自适应策略引擎使用。

设计哲学（基于钱学森《工程控制论》）：
- **反馈 (Feedback)**：每轮工具执行结果回送到控制器，形成闭环
- **稳定性 (Stability)**：通过线性拟合斜率判定误差趋势（收敛/震荡/发散/停滞）
- **可控性 (Controllability)**：输出离散控制状态，供上层策略引擎决策

使用方式：
    >>> from miniagent.core.feedback_controller import FeedbackController, ControlMetrics
    >>> ctrl = FeedbackController(window_size=5)
    >>> for turn in range(1, 10):
    ...     report = ctrl.step(ControlMetrics(
    ...         tool_failure_rate=0.0,
    ...         tool_repeat_rate=0.1,
    ...         error_estimate=0.3,
    ...         turn_number=turn,
    ...     ))
    ...     print(report.state, report.error_value)

详见 ``docs/CYBERNETICS_PLAN.md`` Phase 1。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class ControlState(enum.Enum):
    """控制状态枚举。"""

    STABLE = "stable"          # 误差稳定下降
    OSCILLATING = "oscillating"  # 误差震荡
    DIVERGING = "diverging"    # 误差递增，趋向发散
    STUCK = "stuck"            # 误差停滞，无明显进展
    CONVERGED = "converged"    # 误差接近零，已收敛


@dataclass
class ControlMetrics:
    """单步反馈指标。

    由 ``FeedbackController.step()`` 的调用方构造。
    所有字段取值范围 0.0-1.0（越低越好），除 ``turn_number``。
    """

    tool_failure_rate: float = 0.0       # 本轮工具调用失败比例
    tool_repeat_rate: float = 0.0        # 本轮重复工具调用比例
    error_estimate: float = 0.5          # 综合误差估计（0=完美，1=完全偏离）
    turn_number: int = 0                 # 当前 ReAct 轮次号


@dataclass
class ControlReport:
    """单步控制报告。

    每次 ``step()`` 返回，供日志、调试或自适应策略使用。
    """

    state: ControlState
    error_value: float
    error_slope: float                   # 线性拟合斜率（负=收敛，正=发散）
    stability_index: float               # 0.0-1.0，越高越稳定
    turn_number: int
    recommendation: str = ""             # 人类可读的建议


@dataclass
class FeedbackController:
    """ReAct 循环的反馈控制器。

    每轮调用 :meth:`step` 更新误差信号和收敛趋势。
    无外部依赖，纯 CPU 计算。

    Args:
        window_size: 用于趋势分析的滑动窗口大小（默认 5 轮）
        convergence_threshold: 判定收敛的误差绝对值（默认 0.05，要求极高稳定性）
        slope_diverge_threshold: 判定发散的斜率阈值（默认 0.05/轮）
        slope_stuck_threshold: 判定停滞的斜率绝对值（默认 0.01/轮）
    """

    window_size: int = 5
    convergence_threshold: float = 0.05
    slope_diverge_threshold: float = 0.05
    slope_stuck_threshold: float = 0.01

    _error_history: list[float] = field(default_factory=list, repr=False, init=False)
    _turn_number: int = field(default=0, repr=False, init=False)

    def reset(self) -> None:
        """重置控制器状态（新会话或重规划时调用）。"""
        self._error_history.clear()
        self._turn_number = 0

    def step(self, metrics: ControlMetrics | None = None) -> ControlReport:
        """单步更新：计算误差、收敛趋势和控制状态。

        Args:
            metrics: 本轮测量指标；None 时使用默认（error_estimate=0.5）

        Returns:
            本轮控制报告
        """
        if metrics is None:
            metrics = ControlMetrics()

        # 计算综合误差
        error = self._compute_error(metrics)
        self._error_history.append(error)
        self._turn_number = metrics.turn_number or self._turn_number + 1

        # 计算误差趋势（线性拟合斜率）
        slope = self._compute_slope()
        stability = self._compute_stability(slope)
        state = self._classify_state(error, slope)

        recommendation = self._recommend(state, error, slope)

        return ControlReport(
            state=state,
            error_value=round(error, 4),
            error_slope=round(slope, 4),
            stability_index=round(stability, 4),
            turn_number=self._turn_number,
            recommendation=recommendation,
        )

    # 误差分量权重：当前误差权重最高(0.5)，其次是变化率(0.3)和加速度(0.2)。
    # 这样的权重分配使控制器对即时误差更敏感，同时兼顾趋势。
    _WEIGHT_CURRENT = 0.5
    _WEIGHT_VELOCITY = 0.3
    _WEIGHT_ACCELERATION = 0.2

    def _compute_error(self, metrics: ControlMetrics) -> float:
        """综合误差 = w1*error_estimate + w2*tool_failure + w3*tool_repeat。"""
        error_est = metrics.error_estimate if metrics.error_estimate is not None else 0.5
        return (
            self._WEIGHT_CURRENT * error_est
            + self._WEIGHT_VELOCITY * metrics.tool_failure_rate
            + self._WEIGHT_ACCELERATION * metrics.tool_repeat_rate
        )

    def _compute_slope(self) -> float:
        """用最小二乘法计算最近 window_size 轮误差的线性拟合斜率。

        斜率 < 0 表示误差递减（收敛），> 0 表示递增（发散）。
        不足 2 个数据点时返回 0。
        """
        history = self._error_history
        n = len(history)
        if n < 2:
            return 0.0

        window = history[-self.window_size:]
        m = len(window)
        if m < 2:
            return 0.0

        # 简单线性回归: y = a + b*x, 求 b
        x_mean = (m - 1) / 2.0
        y_mean = sum(window) / m

        numerator = sum((i - x_mean) * (window[i] - y_mean) for i in range(m))
        denominator = sum((i - x_mean) ** 2 for i in range(m))

        if denominator == 0:
            return 0.0
        return numerator / denominator

    def _compute_stability(self, slope: float) -> float:
        """稳定性指数 0-1。

        综合考虑：
        - 斜率绝对值越小越稳定
        - 历史数据越多越可信
        """
        # 基于斜率的稳定性
        slope_stability = max(0.0, 1.0 - abs(slope) / self.slope_diverge_threshold)

        # 数据量置信度（最少 3 轮开始有参考价值）
        n = len(self._error_history)
        confidence = min(1.0, n / max(3, self.window_size))

        return slope_stability * 0.85 + confidence * 0.15

    def _classify_state(self, error: float, slope: float) -> ControlState:
        """根据误差和斜率分类当前状态。"""
        if slope > self.slope_diverge_threshold:
            return ControlState.DIVERGING

        # 震荡检测：最近几轮误差上下交替（优先级高，与绝对误差无关）
        if self._is_oscillating():
            return ControlState.OSCILLATING

        # 收敛：要求足够低的误差 + 至少 3 轮执行历史，避免过早收敛
        if error < self.convergence_threshold and len(self._error_history) >= 3:
            return ControlState.CONVERGED

        if abs(slope) < self.slope_stuck_threshold and len(self._error_history) >= 3:
            return ControlState.STUCK

        return ControlState.STABLE

    def _is_oscillating(self) -> bool:
        """检测误差是否在最近 4+ 轮内持续上下交替。

        要求至少 4 个数据点，排除收敛尾部的正常波动，
        只统计幅度超过 0.05 的方向变化，避免 3 点微抖误触发。
        """
        history = self._error_history[-self.window_size:]
        if len(history) < 4:
            return False
        # 误差已很低时，正常波动不算震荡
        if history[-1] < self.convergence_threshold:
            return False

        direction_changes = 0
        for i in range(2, len(history)):
            prev_dir = history[i - 1] - history[i - 2]
            curr_dir = history[i] - history[i - 1]
            if abs(prev_dir) > 0.05 and abs(curr_dir) > 0.05 and (prev_dir > 0) != (curr_dir > 0):
                direction_changes += 1

        # 超过半数的轮次在反转方向 → 震荡
        return direction_changes >= len(history) // 2 + 1

    def _recommend(self, state: ControlState, error: float, slope: float) -> str:
        """生成人类可读的建议。"""
        recommendations = {
            ControlState.CONVERGED: f"误差 {error:.3f}，已接近目标，可结束。",
            ControlState.STABLE: f"误差 {error:.3f}，斜率 {slope:+.4f}，正常推进。",
            ControlState.OSCILLATING: (
                f"误差 {error:.3f}，呈震荡趋势，建议简化问题或减少工具调用。"
            ),
            ControlState.DIVERGING: (
                f"误差 {error:.3f}，斜率 {slope:+.4f}，趋向发散，建议提前终止或重规划。"
            ),
            ControlState.STUCK: (
                f"误差 {error:.3f}，斜率 {slope:+.4f}，陷入停滞，建议切换策略或重新规划。"
            ),
        }
        return recommendations.get(state, "")

    @property
    def error_history(self) -> list[float]:
        """只读误差历史（副本）。"""
        return list(self._error_history)

    def get_latest_error(self) -> float | None:
        """最近一次误差值；无历史时返回 None。"""
        return self._error_history[-1] if self._error_history else None


__all__ = [
    "ControlState",
    "ControlMetrics",
    "ControlReport",
    "FeedbackController",
]
