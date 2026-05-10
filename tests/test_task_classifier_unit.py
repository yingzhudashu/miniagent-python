"""任务难度分类：映射函数与 JSON 解析（无网络）。"""

from miniagent.core.task_classifier import (
    TaskDifficulty,
    default_step_thinking_for_difficulty,
    exec_merge_for_simple_path,
    planner_merge_for_difficulty,
)


def test_planner_merge_scales_with_difficulty() -> None:
    low = planner_merge_for_difficulty(TaskDifficulty.NORMAL)
    mid = planner_merge_for_difficulty(TaskDifficulty.MEDIUM)
    high = planner_merge_for_difficulty(TaskDifficulty.COMPLEX)
    assert low["thinking_budget"] < high["thinking_budget"]
    assert mid["thinking_budget"] <= high["thinking_budget"]


def test_default_step_thinking_mapping() -> None:
    assert default_step_thinking_for_difficulty(TaskDifficulty.NORMAL) == "low"
    assert default_step_thinking_for_difficulty(TaskDifficulty.MEDIUM) == "medium"
    assert default_step_thinking_for_difficulty(TaskDifficulty.COMPLEX) == "high"


def test_exec_merge_simple_path() -> None:
    m = exec_merge_for_simple_path()
    assert "thinking_level" in m and "thinking_budget" in m
