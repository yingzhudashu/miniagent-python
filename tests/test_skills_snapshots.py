"""Tests for miniagent.assistant.skills.snapshots."""

from __future__ import annotations

from miniagent.assistant.skills.snapshots import (
    apply_skill_snapshots_to_state,
    get_skill_prompts_from_state,
    get_skill_toolboxes_from_state,
    join_skill_prompts,
)


def test_get_skill_toolboxes_from_state_none() -> None:
    assert get_skill_toolboxes_from_state(None) == []


def test_get_skill_toolboxes_from_state_empty() -> None:
    assert get_skill_toolboxes_from_state({}) == []


def test_get_skill_toolboxes_from_state_present() -> None:
    state = {"skill_toolboxes": ["tb1", "tb2"]}
    result = get_skill_toolboxes_from_state(state)
    assert result == ["tb1", "tb2"]
    assert result is not state["skill_toolboxes"]  # copy


def test_get_skill_prompts_from_state_none() -> None:
    assert get_skill_prompts_from_state(None) == []


def test_join_skill_prompts_none() -> None:
    assert join_skill_prompts(None) is None
    assert join_skill_prompts([]) is None


def test_join_skill_prompts_empty_strings() -> None:
    assert join_skill_prompts(["", "  ", None]) is None


def test_join_skill_prompts_normal() -> None:
    result = join_skill_prompts(["prompt A", "prompt B"])
    assert result == "prompt A\n\nprompt B"


def test_join_skill_prompts_skips_empty() -> None:
    result = join_skill_prompts(["A", "", "  ", "B"])
    assert result == "A\n\nB"


def test_apply_skill_snapshots_to_state() -> None:
    state: dict = {}
    apply_skill_snapshots_to_state(state, skill_toolboxes=["tb"], skill_prompts=["p"])
    assert state["skill_toolboxes"] == ["tb"]
    assert state["skill_prompts"] == ["p"]
