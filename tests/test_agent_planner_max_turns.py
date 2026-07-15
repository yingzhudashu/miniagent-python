"""规划建议 max_turns 与合并配置的 max 语义。"""

from __future__ import annotations

from miniagent.agent.config import merge_agent_config
from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.planning import SuggestedConfig


def _apply_suggested_max_turns(merged: AgentConfig, sc: SuggestedConfig) -> AgentConfig:
    """与 run_agent 内对 plan.suggested_config.max_turns 的合并一致。"""
    overrides: dict = {}
    if sc.max_turns is not None:
        overrides["max_turns"] = max(merged.max_turns, sc.max_turns)
    if overrides:
        return merge_agent_config(merged, overrides)
    return merged


def test_planner_suggested_max_turns_does_not_shrink_base() -> None:
    merged = AgentConfig(max_turns=100)
    sc = SuggestedConfig(max_turns=5)
    out = _apply_suggested_max_turns(merged, sc)
    assert out.max_turns == 100


def test_planner_suggested_max_turns_can_raise_above_base() -> None:
    merged = AgentConfig(max_turns=100)
    sc = SuggestedConfig(max_turns=300)
    out = _apply_suggested_max_turns(merged, sc)
    assert out.max_turns == 300
