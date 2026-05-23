"""State Observer 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from miniagent.core.state_observer import AgentState, StateObserver


class TestStateObserver:
    """StateObserver 行为测试。"""

    def test_initial_state(self) -> None:
        obs = StateObserver()
        state = obs.end_turn()
        assert state.total_turns == 1
        assert state.total_tool_calls == 0
        assert state.tool_success_rate == 1.0

    def test_record_tool_success(self) -> None:
        obs = StateObserver()
        obs.record_tool("web_search", success=True)
        obs.record_tool("read_file", success=True)
        state = obs.end_turn()
        assert state.total_tool_calls == 2
        assert state.tool_success_rate == 1.0
        assert state.unique_tool_call_ratio == 1.0

    def test_record_tool_failure(self) -> None:
        obs = StateObserver()
        obs.record_tool("web_search", success=True)
        obs.record_tool("web_search", success=False)
        state = obs.end_turn()
        assert state.total_tool_calls == 2
        assert state.tool_success_rate == 0.5

    def test_tool_dedup_ratio(self) -> None:
        """同一工具多次调用应降低去重率。"""
        obs = StateObserver()
        # 第 1 轮：3 次相同工具
        obs.record_tool("web_search", success=True)
        obs.record_tool("web_search", success=True)
        obs.record_tool("web_search", success=True)
        state = obs.end_turn()
        # 1 unique / 3 calls = 0.333
        assert abs(state.unique_tool_call_ratio - 1.0 / 3.0) < 0.01

    def test_multi_turn_dedup(self) -> None:
        """多轮不同工具应提高去重率。"""
        obs = StateObserver()
        # 第 1 轮：1 个工具，调用 1 次
        obs.record_tool("web_search", True)
        obs.end_turn()
        # 第 2 轮：1 个不同工具，调用 1 次
        obs.record_tool("read_file", True)
        state = obs.end_turn()
        # 2 轮: 2 unique tools / 2 calls = 1.0
        assert state.unique_tool_call_ratio == 1.0

    def test_context_manager_integration(self) -> None:
        """传入 context_manager 时应计算上下文使用率。"""
        obs = StateObserver()
        mock_cm = MagicMock()
        mock_cm._total_tokens_estimate = 64000
        mock_cm._context_window = 128000
        state = obs.end_turn(context_manager=mock_cm)
        assert abs(state.context_usage_ratio - 0.5) < 0.01
        assert state.token_budget_remaining > 0

    def test_controller_integration(self) -> None:
        """传入 feedback_controller 时应读取最新误差。"""
        obs = StateObserver()
        mock_ctrl = MagicMock()
        mock_ctrl.get_latest_error.return_value = 0.25
        state = obs.end_turn(controller=mock_ctrl)
        assert abs(state.convergence_velocity - 0.25) < 0.01

    def test_readable_state(self) -> None:
        """可读报告应包含关键指标。"""
        obs = StateObserver()
        state = AgentState(
            total_turns=5,
            total_tool_calls=12,
            context_usage_ratio=0.45,
            tool_success_rate=0.9,
            unique_tool_call_ratio=0.75,
            token_budget_remaining=50000,
        )
        report = obs.readable_state(state)
        assert "5" in report
        assert "12" in report
        assert "45%" in report or "90%" in report

    def test_reset_clears_all(self) -> None:
        obs = StateObserver()
        obs.record_tool("web_search", True)
        obs.end_turn()
        obs.reset()
        state = obs.end_turn()
        assert state.total_turns == 1
        assert state.total_tool_calls == 0

    def test_empty_turn_not_counted_for_rate(self) -> None:
        """空轮（无工具调用）不应影响成功率计算。"""
        obs = StateObserver()
        obs.end_turn()  # 空轮
        state = obs.end_turn()  # 又一空轮
        # 没有工具调用时，成功率应保持 1.0
        assert state.tool_success_rate == 1.0
