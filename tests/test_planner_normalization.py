"""Planner post-processing tests for minimal execution paths."""

from __future__ import annotations

from miniagent.core.planner import _normalize_plan_steps
from miniagent.types.planning import PlanStep


def test_normalize_plan_steps_merges_duplicate_file_reads_and_repairs_depends_on() -> None:
    steps = [
        PlanStep(3, "读取 config.json", ["file_read"], "config.json", "文件内容"),
        PlanStep(5, "再次读取 config.json", ["file_read"], "config.json", "文件内容"),
        PlanStep(8, "分析 config.json 配置", [], "文件内容", "分析结论", depends_on=5),
    ]

    normalized = _normalize_plan_steps(steps)

    assert [s.step_number for s in normalized] == [1, 2]
    assert normalized[0].description == "读取 config.json"
    assert normalized[1].depends_on == 1


def test_normalize_plan_steps_drops_empty_steps_and_dedupes_toolboxes() -> None:
    steps = [
        PlanStep(1, "", ["fs"], "", ""),
        PlanStep(2, "运行测试", ["exec", "exec"], "", "测试结果"),
    ]

    normalized = _normalize_plan_steps(steps)

    assert len(normalized) == 1
    assert normalized[0].step_number == 1
    assert normalized[0].required_toolboxes == ["exec"]
