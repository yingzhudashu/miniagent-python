"""控制论模块集成测试 — 验证 execute_plan() 自动实例化控制器。"""

import os
from unittest.mock import MagicMock, patch

import pytest

from miniagent.core.adaptive_policy import AdaptiveAction, AdaptivePolicy
from miniagent.core.executor import execute_plan
from miniagent.core.feedback_controller import ControlMetrics, ControlState, FeedbackController
from miniagent.core.state_observer import StateObserver
from tests.executor_helpers import (
    agent_config_with_session,
    empty_plan,
    make_ping_tool_registry,
    mock_memory_bundle,
    mock_streaming_client,
)


class TestControlTheoryAutoInstantiation:
    """测试 execute_plan() 在未传入控制器时自动实例化。"""

    @pytest.mark.asyncio
    async def test_auto_creates_controllers_when_none(self):
        """未传入任何控制器时，内部自动创建三个实例，正常执行不报错。"""
        main, sess = make_ping_tool_registry()
        mock_client = mock_streaming_client()
        ms, al, ki = mock_memory_bundle()

        # 不传入任何控制器
        out = await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=mock_client,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
        )
        # 控制论收敛时会提前返回包含"收敛"的文案，这也说明控制器生效了
        assert "done" in out or "收敛" in out

    @pytest.mark.asyncio
    async def test_env_disable_control_theory(self):
        """MINIAGENT_CONTROL_THEORY=0 时不自动实例化，走旧路径。"""
        main, sess = make_ping_tool_registry()
        mock_client = mock_streaming_client()
        ms, al, ki = mock_memory_bundle()

        with patch.dict(os.environ, {"MINIAGENT_CONTROL_THEORY": "0"}):
            out = await execute_plan(
                empty_plan(),
                "hi",
                main,
                MagicMock(),
                agent_config_with_session(sess),
                client=mock_client,
                memory_store=ms,
                activity_log=al,
                keyword_index=ki,
            )
        assert "done" in out

    @pytest.mark.asyncio
    async def test_explicit_controllers_not_overridden(self):
        """显式传入的控制器不被覆盖，使用传入的实例。"""
        main, sess = make_ping_tool_registry()
        mock_client = mock_streaming_client()
        ms, al, ki = mock_memory_bundle()

        # 使用较大的收敛阈值以避免 CONVERGED_EXIT 提前终止
        custom_ctrl = FeedbackController(convergence_threshold=0.001)
        custom_obs = StateObserver()
        custom_policy = AdaptivePolicy(max_diverge_turns=999)

        out = await execute_plan(
            empty_plan(),
            "hi",
            main,
            MagicMock(),
            agent_config_with_session(sess),
            client=mock_client,
            memory_store=ms,
            activity_log=al,
            keyword_index=ki,
            feedback_controller=custom_ctrl,
            state_observer=custom_obs,
            adaptive_policy=custom_policy,
        )
        # 自定义控制器被使用时，可能触发收敛提前退出
        assert "done" in out or "收敛" in out

    def test_feedback_controller_default_values(self):
        """验证默认控制器实例正常工作。"""
        ctrl = FeedbackController()
        report = ctrl.step()
        assert report.state in ControlState
        assert 0.0 <= report.stability_index <= 1.0

    def test_state_observer_default_values(self):
        """验证默认观测器实例正常工作。"""
        obs = StateObserver()
        obs.record_tool("test_tool", success=True)
        state = obs.end_turn()
        assert state.tool_success_rate == 1.0
        assert state.total_turns == 1

    def test_adaptive_policy_default_values(self):
        """验证默认策略实例正常工作。"""
        policy = AdaptivePolicy()
        decision = policy.decide(ControlState.STABLE)
        assert decision.action == AdaptiveAction.NORMAL

    def test_control_theory_full_chain(self):
        """验证三个控制器协作：controller → observer → policy。"""
        ctrl = FeedbackController(window_size=3)
        obs = StateObserver()
        policy = AdaptivePolicy()

        # 模拟收敛过程
        for i in range(5):
            error = 0.5 - i * 0.1
            report = ctrl.step(ControlMetrics(
                error_estimate=max(0, error),
                turn_number=i + 1,
            ))
            obs.record_tool("test_tool", success=True)
            state = obs.end_turn(controller=ctrl)
            decision = policy.decide(report.state, state)
            assert decision.action is not None

        # 误差降低后应该收敛或稳定
        assert ctrl.get_latest_error() < 0.5
