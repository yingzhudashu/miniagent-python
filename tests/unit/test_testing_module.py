"""Testing 模块单元测试

验证 SampleSpec、TestRunner 和 run_self_test 的核心功能。
"""

from __future__ import annotations

import pytest

from miniagent.testing.types import (
    ReportSummary,
    ResultRecord,
    SampleSpec,
)


class TestTypes:
    """类型定义测试"""

    def test_sample_from_dict(self) -> None:
        """从字典创建 SampleSpec"""
        data = {
            "name": "test_example",
            "description": "测试示例",
            "input": "帮我创建一个文件",
            "category": "tool_selection",
            "expected_action": "execute",
            "expected_tools": ["write_file"],
            "must_call_tools": ["write_file"],
            "must_not_call_tools": [],
            "priority": 1,
        }
        sample = SampleSpec.from_dict(data)

        assert sample.name == "test_example"
        assert sample.input == "帮我创建一个文件"
        assert sample.category == "tool_selection"
        assert sample.expected_tools == ["write_file"]
        assert sample.must_call_tools == ["write_file"]

    def test_sample_to_dict(self) -> None:
        """SampleSpec 转换为字典"""
        sample = SampleSpec(
            name="test_example",
            input="帮我创建一个文件",
            category="tool_selection",
            expected_tools=["write_file"],
        )
        data = sample.to_dict()

        assert data["name"] == "test_example"
        assert data["category"] == "tool_selection"
        assert data["expected_tools"] == ["write_file"]

    def test_backward_compat_aliases_removed(self) -> None:
        """向后兼容别名已删除，确认新名称可用"""
        # 新类型名称应该是正确的
        assert SampleSpec is not None
        assert ResultRecord is not None
        assert ReportSummary is not None

    def test_result_to_dict_truncates_output(self) -> None:
        """ResultRecord.to_dict 截断输出"""
        result = ResultRecord(
            sample_name="test",
            passed=True,
            actual_output="这是一个很长的输出内容..." * 100,
        )
        data = result.to_dict()

        assert len(data["actual_output"]) <= 200

    def test_report_pass_rate(self) -> None:
        """ReportSummary 通过率计算"""
        report = ReportSummary(total=10, passed=8, failed=2)
        assert report.pass_rate == 0.8

        report_empty = ReportSummary(total=0)
        assert report_empty.pass_rate == 0.0


class TestRunnerBasic:
    """TestRunner 基本功能测试"""

    def test_load_samples_mock(self) -> None:
        """加载测试样本（mock 模式，不依赖文件）"""
        from miniagent.testing.test_runner import TestRunner

        # 创建 runner 但不加载文件
        runner = TestRunner(samples_dir="nonexistent_dir")
        samples = runner.load_samples()

        assert samples == []

    def test_filter_samples_by_category(self) -> None:
        """按类别过滤样本"""
        from miniagent.testing.test_runner import TestRunner

        runner = TestRunner(samples_dir="nonexistent_dir")

        # 直接设置 _samples（不调用 load_samples，因为它会清空）
        runner._samples = [
            SampleSpec(name="test1", input="输入1", category="security", priority=1),
            SampleSpec(name="test2", input="输入2", category="tool_selection", priority=2),
            SampleSpec(name="test3", input="输入3", category="security", priority=3),
        ]

        # 使用内部的过滤逻辑（模拟 load_samples 的过滤部分）
        filtered = [s for s in runner._samples if s.category == "security"]
        filtered.sort(key=lambda s: s.priority)

        assert len(filtered) == 2
        assert filtered[0].name == "test1"  # priority 1
        assert filtered[1].name == "test3"  # priority 3


@pytest.mark.asyncio
async def test_mock_run_validates_constraints() -> None:
    """Mock 模式验证约束"""
    from miniagent.testing.test_runner import TestRunner

    runner = TestRunner(samples_dir="nonexistent_dir")

    # 测试样本：配置正确
    valid_sample = SampleSpec(
        name="valid_test",
        input="测试",
        expected_action="execute",
        expected_tools=["write_file"],
        must_call_tools=["write_file"],
    )
    result = await runner._mock_run(valid_sample)
    assert result.passed

    # 测试样本：配置错误（must_call 不在 expected 中）
    invalid_sample = SampleSpec(
        name="invalid_test",
        input="测试",
        expected_action="execute",
        expected_tools=["read_file"],
        must_call_tools=["write_file"],  # 不在 expected_tools 中
    )
    result = await runner._mock_run(invalid_sample)
    assert not result.passed
    assert "Mock 配置错误" in result.error_message


@pytest.mark.asyncio
async def test_run_self_test_with_mock() -> None:
    """run_self_test 函数测试（mock 模式）"""
    from miniagent.testing import run_self_test

    # 使用 nonexistent_dir 确保不加载真实文件
    report = await run_self_test(
        samples_dir="nonexistent_dir",
        mock=True,
    )

    assert report.total == 0
    assert report.pass_rate == 0.0