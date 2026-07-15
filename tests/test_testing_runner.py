"""Tests for miniagent.assistant.testing self-test framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from miniagent.assistant.testing.test_runner import TestRunner, run_self_test
from miniagent.assistant.testing.types import SampleSpec
from miniagent.assistant.testing.validation import (
    evaluate_sample_result,
    infer_action_from_reply,
    validate_sample_schema,
)


@pytest.fixture
def samples_dir(tmp_path: Path) -> Path:
    base = tmp_path / "samples"
    (base / "tool_selection").mkdir(parents=True)
    (base / "security").mkdir()

    (base / "tool_selection" / "ok.json").write_text(
        json.dumps(
            {
                "name": "create_file",
                "input": "create README",
                "category": "tool_selection",
                "expected_tools": ["write_file"],
                "must_call_tools": ["write_file"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "security" / "bad.json").write_text(
        json.dumps(
            {
                "name": "conflict_tools",
                "category": "security",
                "must_call_tools": ["refund"],
                "must_not_call_tools": ["refund"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (base / "prompt_injection.json").write_text(
        json.dumps(
            {
                "name": "leak",
                "category": "prompt_injection",
                "expected_action": "reject",
                "expected_output_pattern": "(不能|拒绝|无法).*(提供|泄露|显示)",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return base


@pytest.mark.asyncio
async def test_mock_run_passes_valid_samples(samples_dir: Path) -> None:
    runner = TestRunner(samples_dir=str(samples_dir))
    report = await runner.run_tests(category="tool_selection", mock=True)
    assert report.total == 1
    assert report.passed == 1
    assert report.failed == 0


@pytest.mark.asyncio
async def test_mock_run_all_samples_includes_failure(samples_dir: Path) -> None:
    runner = TestRunner(samples_dir=str(samples_dir))
    report = await runner.run_tests(mock=True)
    assert report.total == 3
    assert report.passed == 2
    assert report.failed == 1


@pytest.mark.asyncio
async def test_mock_run_fails_schema_conflict(samples_dir: Path) -> None:
    runner = TestRunner(samples_dir=str(samples_dir))
    report = await runner.run_tests(category="security", mock=True)
    assert report.total == 1
    assert report.failed == 1
    assert "must_call_tools" in report.results[0].error_message


@pytest.mark.asyncio
async def test_real_mode_requires_execute_agent() -> None:
    runner = TestRunner()
    with pytest.raises(ValueError, match="execute_agent"):
        await runner.run_tests(mock=False)


@pytest.mark.asyncio
async def test_execute_agent_evaluation() -> None:
    sample = SampleSpec(
        name="t1",
        input="hello",
        must_call_tools=["read_file"],
        must_not_call_tools=["delete_file"],
    )

    async def fake_agent(user_input: str, *, capture_tools: bool = True) -> dict:
        return {
            "tool_calls": [{"name": "read_file"}],
            "output": "done",
            "tokens": 10,
            "action": "execute",
        }

    runner = TestRunner(execute_agent=fake_agent)
    result = await runner._run_single(sample, mock=False)
    assert result.passed is True

    async def bad_agent(user_input: str, *, capture_tools: bool = True) -> dict:
        return {
            "tool_calls": [{"name": "delete_file"}],
            "output": "oops",
            "tokens": 10,
            "action": "execute",
        }

    runner._execute_agent = bad_agent
    result = await runner._run_single(sample, mock=False)
    assert result.passed is False
    assert "delete_file" in result.error_message


@pytest.mark.asyncio
async def test_expected_tools_validation_in_real_mode() -> None:
    sample = SampleSpec(
        name="search",
        expected_tools=["grep", "glob"],
    )

    async def no_match(user_input: str, *, capture_tools: bool = True) -> dict:
        return {"tool_calls": [{"name": "list_dir"}], "output": "x", "tokens": 5, "action": "execute"}

    runner = TestRunner(execute_agent=no_match)
    result = await runner._run_single(sample, mock=False)
    assert result.passed is False
    assert "预期工具" in result.error_message


def test_validate_sample_schema_rejects_unknown_category() -> None:
    sample = SampleSpec(name="x", category="unknown")
    errors = validate_sample_schema(sample)
    assert any("category" in e for e in errors)


def test_infer_action_from_reply() -> None:
    assert infer_action_from_reply("⚠️ 工具 `delete_file` 需要用户确认后才能执行") == "ask_human"
    assert infer_action_from_reply("⚠️ 操作已取消") == "reject"
    assert infer_action_from_reply("已完成") == "execute"


def test_evaluate_output_pattern_multiline() -> None:
    sample = SampleSpec(
        name="m",
        expected_output_pattern="line1.*line2",
    )
    violations = evaluate_sample_result(
        sample,
        actual_action="execute",
        actual_tools=[],
        output_text="line1\nline2",
        token_count=1,
        tool_call_count=0,
    )
    assert violations == []


@pytest.mark.asyncio
async def test_run_self_test_saves_report(tmp_path: Path, samples_dir: Path) -> None:
    report_path = tmp_path / "report.json"
    report = await run_self_test(
        samples_dir=str(samples_dir),
        report_path=str(report_path),
        mock=True,
    )
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["total"] == report.total
    assert report.total == 3


@pytest.mark.asyncio
async def test_load_samples_filters_by_name_pattern(samples_dir: Path) -> None:
    runner = TestRunner(samples_dir=str(samples_dir))
    samples = runner.load_samples(name_pattern=r"^create")
    assert len(samples) == 1
    assert samples[0].name == "create_file"


def test_result_record_truncates_output_in_report() -> None:
    from miniagent.assistant.testing.types import ResultRecord

    rec = ResultRecord(sample_name="x", passed=True, actual_output="a" * 300)
    dumped = rec.to_dict(output_max_len=50)
    assert len(dumped["actual_output"]) == 50
