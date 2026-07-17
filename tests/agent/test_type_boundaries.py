"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from miniagent.agent.plan_utils import _parse_depends_on, _resolve_step_depends_on
from miniagent.agent.planner import _dict_to_plan


def test_dependency_parsers_reject_arbitrary_objects() -> None:
    marker = object()
    assert _resolve_step_depends_on(marker, {}) is None
    assert _parse_depends_on(marker) is None

def test_invalid_step_thinking_level_falls_back() -> None:
    plan = _dict_to_plan(
        {
            "defaultStepThinkingLevel": "low",
            "steps": [{"description": "step", "thinkingLevel": "invalid"}],
        }
    )
    assert plan.steps[0].thinking_level == "low"
