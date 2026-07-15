"""Planner post-processing tests for minimal execution paths."""

from __future__ import annotations

from miniagent.agent.planner import (
    _completed_work_context,
    _dedupe_toolboxes,
    _normalize_plan_steps,
)
from miniagent.agent.types.config import AgentConfig, SessionBindingConfig
from miniagent.agent.types.planning import PlanStep


def _config_with_history(history: list[dict[str, str]]) -> AgentConfig:
    return AgentConfig(
        session_config=SessionBindingConfig(conversation_history=history)
    )


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


def test_dedupe_toolboxes_merges_plan_and_step_level() -> None:
    steps = [
        PlanStep(1, "搜索", ["web"], "", ""),
        PlanStep(2, "写文件", ["fs"], "", ""),
    ]

    result = _dedupe_toolboxes(["fs", "exec", "fs"], steps)

    assert result == ["fs", "exec", "web"]


def test_dedupe_toolboxes_filters_empty_and_non_list() -> None:
    steps = [PlanStep(1, "x", ["web"], "", "")]

    assert _dedupe_toolboxes(None, steps) == ["web"]
    assert _dedupe_toolboxes(["", "  ", "web"], steps) == ["web"]


def test_completed_work_context_extracts_keyword_messages() -> None:
    cfg = _config_with_history(
        [
            {"content": "你好，今天天气不错"},
            {"content": "已通过 read_file 读取 config.py"},
            {"content": "分析完成，测试通过 pytest"},
        ]
    )

    result = _completed_work_context(cfg)

    assert "最近已完成工作" in result
    assert "read_file" in result
    assert "pytest" in result
    assert "天气" not in result


def test_completed_work_context_empty_without_relevant_history() -> None:
    assert _completed_work_context(None) == ""
    assert _completed_work_context(_config_with_history([])) == ""
    assert (
        _completed_work_context(_config_with_history([{"content": "普通闲聊"}]))
        == ""
    )
