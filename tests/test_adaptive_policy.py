"""Adaptive Policy 单元测试。"""

from __future__ import annotations

from miniagent.core.adaptive_policy import AdaptiveAction, AdaptivePolicy
from miniagent.core.feedback_controller import ControlState
from miniagent.core.state_observer import AgentState


class TestAdaptivePolicy:
    """AdaptivePolicy 行为测试。"""

    def test_stable_returns_normal(self) -> None:
        policy = AdaptivePolicy()
        decision = policy.decide(ControlState.STABLE)
        assert decision.action == AdaptiveAction.NORMAL

    def test_converged_returns_exit(self) -> None:
        policy = AdaptivePolicy()
        decision = policy.decide(ControlState.CONVERGED)
        assert decision.action == AdaptiveAction.CONVERGED_EXIT

    def test_oscillating_returns_simplify(self) -> None:
        policy = AdaptivePolicy()
        decision = policy.decide(ControlState.OSCILLATING)
        assert decision.action == AdaptiveAction.SIMPLIFY
        assert "reduce_tools" in decision.config_overrides

    def test_diverging_escalates_to_terminate(self) -> None:
        """发散持续超过 max_diverge_turns 后应触发 TERMINATE。"""
        policy = AdaptivePolicy(max_diverge_turns=3)
        # 前 2 次应返回 SIMPLIFY
        for _ in range(2):
            decision = policy.decide(ControlState.DIVERGING)
            assert decision.action == AdaptiveAction.SIMPLIFY
        # 第 3 次应触发 TERMINATE
        decision = policy.decide(ControlState.DIVERGING)
        assert decision.action == AdaptiveAction.TERMINATE

    def test_stuck_escalates_to_replan(self) -> None:
        """停滞持续超过 max_turns_before_replan 后应触发 REPLAN。"""
        policy = AdaptivePolicy(max_turns_before_replan=3)
        for _ in range(2):
            decision = policy.decide(ControlState.STUCK)
            assert decision.action == AdaptiveAction.SIMPLIFY
        decision = policy.decide(ControlState.STUCK)
        assert decision.action == AdaptiveAction.REPLAN

    def test_context_triggers_compress(self) -> None:
        """上下文使用率超过阈值应触发 COMPRESS。"""
        policy = AdaptivePolicy(max_context_ratio=0.85)
        state = AgentState(context_usage_ratio=0.9)
        decision = policy.decide(ControlState.STABLE, agent_state=state)
        assert decision.action == AdaptiveAction.COMPRESS

    def test_context_below_threshold_no_compress(self) -> None:
        """上下文使用率低于阈值时不触发 COMPRESS。"""
        policy = AdaptivePolicy(max_context_ratio=0.85)
        state = AgentState(context_usage_ratio=0.5)
        decision = policy.decide(ControlState.STABLE, agent_state=state)
        assert decision.action == AdaptiveAction.NORMAL

    def test_reset_clears_counters(self) -> None:
        policy = AdaptivePolicy(max_diverge_turns=2)
        policy.decide(ControlState.DIVERGING)
        policy.decide(ControlState.DIVERGING)
        policy.reset()
        # 重置后重新计数，应返回 SIMPLIFY 而非 TERMINATE
        decision = policy.decide(ControlState.DIVERGING)
        assert decision.action == AdaptiveAction.SIMPLIFY

    def test_state_transitions_reset_counters(self) -> None:
        """状态切换应重置对应的计数器。"""
        policy = AdaptivePolicy(max_diverge_turns=2, max_turns_before_replan=2)
        # 2 次发散计数
        policy.decide(ControlState.DIVERGING)
        policy.decide(ControlState.DIVERGING)
        # 切到震荡，应重置发散计数
        policy.decide(ControlState.OSCILLATING)
        # 再切回发散，应重新计数
        decision = policy.decide(ControlState.DIVERGING)
        assert decision.action == AdaptiveAction.SIMPLIFY  # 不是 TERMINATE

    def test_reason_not_empty(self) -> None:
        """所有决策应有原因说明。"""
        policy = AdaptivePolicy()
        for state in ControlState:
            decision = policy.decide(state)
            assert decision.reason, f"Missing reason for {state}"
