"""Feedback Controller 单元测试。"""

from __future__ import annotations

from miniagent.core.feedback_controller import (
    ControlMetrics,
    ControlState,
    FeedbackController,
)


class TestFeedbackController:
    """FeedbackController 行为测试。"""

    def test_initial_state(self) -> None:
        ctrl = FeedbackController()
        report = ctrl.step(ControlMetrics(turn_number=1))
        # 初始误差默认 0.5，窗口不足 → STABLE
        assert report.state in (ControlState.STABLE, ControlState.CONVERGED)
        assert report.turn_number == 1

    def test_convergence_detection(self) -> None:
        """误差稳定下降应判定为 CONVERGED。"""
        ctrl = FeedbackController(convergence_threshold=0.1)
        for i in range(1, 8):
            err = max(0.0, 0.6 - i * 0.1)
            report = ctrl.step(ControlMetrics(error_estimate=err, turn_number=i))
        assert report.state == ControlState.CONVERGED

    def test_divergence_detection(self) -> None:
        """误差稳定上升应判定为 DIVERGING。"""
        ctrl = FeedbackController(slope_diverge_threshold=0.03)
        for i in range(1, 8):
            err = min(1.0, 0.1 + i * 0.12)
            report = ctrl.step(ControlMetrics(error_estimate=err, turn_number=i))
        assert report.state == ControlState.DIVERGING

    def test_stuck_detection(self) -> None:
        """误差几乎不变应判定为 STUCK。"""
        ctrl = FeedbackController(slope_stuck_threshold=0.01)
        for i in range(1, 6):
            report = ctrl.step(ControlMetrics(error_estimate=0.5, turn_number=i))
        assert report.state == ControlState.STUCK

    def test_oscillation_detection(self) -> None:
        """误差交替变化应判定为 OSCILLATING。"""
        ctrl = FeedbackController()
        pattern = [0.5, 0.3, 0.6, 0.2, 0.7, 0.1]
        for i, err in enumerate(pattern, 1):
            report = ctrl.step(ControlMetrics(error_estimate=err, turn_number=i))
        assert report.state == ControlState.OSCILLATING

    def test_error_composition(self) -> None:
        """综合误差 = w1*error_estimate + w2*tool_failure + w3*tool_repeat。"""
        ctrl = FeedbackController()
        m = ControlMetrics(
            error_estimate=1.0,
            tool_failure_rate=1.0,
            tool_repeat_rate=1.0,
        )
        report = ctrl.step(m)
        assert abs(report.error_value - 1.0) < 0.01  # 全1 → 综合1

        m2 = ControlMetrics(
            error_estimate=0.0,
            tool_failure_rate=0.0,
            tool_repeat_rate=0.0,
        )
        report2 = ctrl.step(m2)
        assert abs(report2.error_value) < 0.01  # 全0 → 综合0

    def test_none_metrics_uses_default(self) -> None:
        """传入 None 时使用默认指标（error_estimate=0.5）。"""
        ctrl = FeedbackController()
        report = ctrl.step(None)
        assert 0.2 < report.error_value < 0.8  # 默认值在合理范围内

    def test_reset_clears_history(self) -> None:
        ctrl = FeedbackController()
        for i in range(1, 4):
            ctrl.step(ControlMetrics(error_estimate=0.1, turn_number=i))
        assert len(ctrl.error_history) == 3
        ctrl.reset()
        assert len(ctrl.error_history) == 0
        assert ctrl.get_latest_error() is None

    def test_get_latest_error(self) -> None:
        ctrl = FeedbackController()
        assert ctrl.get_latest_error() is None
        # error_estimate=0.3 → weighted 0.5*0.3 = 0.15
        ctrl.step(ControlMetrics(error_estimate=0.3, turn_number=1))
        assert abs(ctrl.get_latest_error() - 0.15) < 0.01

    def test_slope_computation_with_insufficient_data(self) -> None:
        """不足 2 个数据点时斜率应为 0。"""
        ctrl = FeedbackController()
        report = ctrl.step(ControlMetrics(error_estimate=0.5, turn_number=1))
        assert report.error_slope == 0.0

    def test_recommendation_not_empty(self) -> None:
        """每个状态应有建议文案。"""
        ctrl = FeedbackController()
        for state in ControlState:
            r = ctrl._recommend(state, 0.5, 0.0)
            assert r, f"Missing recommendation for {state}"

    def test_stability_index_range(self) -> None:
        """稳定性指数应在 0-1 范围内。"""
        ctrl = FeedbackController()
        for i in range(1, 10):
            report = ctrl.step(ControlMetrics(error_estimate=0.5, turn_number=i))
            assert 0.0 <= report.stability_index <= 1.0
