"""Mini Agent Python — 自测执行器

执行测试样本并生成报告。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from miniagent.testing.types import TestReport, TestResult, TestSample, SampleSpec, ResultRecord
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)


class TestRunner:
    """测试执行器

    加载测试样本、执行测试并生成报告。

    Example:
        runner = TestRunner(samples_dir="tests/evaluation/samples")
        report = await runner.run_tests(category="security")
    """

    def __init__(
        self,
        samples_dir: str = "tests/evaluation/samples",
        *,
        execute_agent: Callable | None = None,
        term_write: Callable | None = None,
    ) -> None:
        """创建测试执行器

        Args:
            samples_dir: 测试样本目录
            execute_agent: Agent 执行函数（None 时使用 mock）
            term_write: 输出函数（用于 CLI 显示）
        """
        self._samples_dir = Path(samples_dir)
        self._execute_agent = execute_agent
        self._term_write = term_write
        self._samples: list[SampleSpec] = []
        self._loaded = False

    def load_samples(self, category: str | None = None, name_pattern: str | None = None) -> list[SampleSpec]:
        """加载测试样本

        Args:
            category: 按类别过滤（None 表示全部）
            name_pattern: 按名称过滤（正则）

        Returns:
            匹配的测试样本列表
        """
        self._samples = []

        if not self._samples_dir.exists():
            _logger.warning("测试样本目录不存在: %s", self._samples_dir)
            return []

        # 遍历所有 JSON 文件
        for json_file in self._samples_dir.rglob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                # 支持单条或多条样本
                if isinstance(data, list):
                    for item in data:
                        sample = SampleSpec.from_dict(item)
                        self._samples.append(sample)
                else:
                    sample = SampleSpec.from_dict(data)
                    self._samples.append(sample)

            except Exception as e:
                _logger.error("加载测试样本失败: %s (%s)", json_file, e)

        # 过滤
        filtered = []
        name_re = re.compile(name_pattern) if name_pattern else None

        for sample in self._samples:
            if category and sample.category != category:
                continue
            if name_re and not name_re.search(sample.name):
                continue
            filtered.append(sample)

        # 按优先级排序
        filtered.sort(key=lambda s: s.priority)

        self._loaded = True
        return filtered

    def list_samples(self) -> list[dict[str, Any]]:
        """列出所有测试样本（摘要形式）"""
        if not self._loaded:
            self.load_samples()

        return [
            {
                "name": s.name,
                "category": s.category,
                "priority": s.priority,
                "description": s.description[:50] if s.description else s.input[:50],
            }
            for s in self._samples
        ]

    async def run_tests(
        self,
        category: str | None = None,
        name_pattern: str | None = None,
        *,
        mock: bool = False,
    ) -> TestReport:
        """运行测试

        Args:
            category: 按类别过滤
            name_pattern: 按名称过滤
            mock: 是否使用 mock 模式（不调用真实 Agent）

        Returns:
            测试报告
        """
        samples = self.load_samples(category, name_pattern)

        report = TestReport(total=len(samples))

        if self._term_write:
            self._term_write(f"\n[cyan]开始运行 {len(samples)} 条测试...[/cyan]\n")

        start_time = time.time()

        for sample in samples:
            try:
                result = await self._run_single(sample, mock=mock)
                report.results.append(result)

                if result.passed:
                    report.passed += 1
                    if self._term_write:
                        self._term_write(f"[green]✓[/green] {sample.name}\n")
                else:
                    report.failed += 1
                    if self._term_write:
                        self._term_write(f"[red]✗[/red] {sample.name}: {result.error_message}\n")

            except Exception as e:
                report.skipped += 1
                report.results.append(TestResult(
                    sample_name=sample.name,
                    passed=False,
                    error_message=str(e),
                ))
                if self._term_write:
                    self._term_write(f"[yellow]⊗[/yellow] {sample.name}: {e}\n")

        report.duration_seconds = time.time() - start_time

        if self._term_write:
            self._term_write(
                f"\n[cyan]测试完成: {report.passed}/{report.total} 通过 "
                f"({report.pass_rate:.1%}, {report.duration_seconds:.1f}s)[/cyan]\n"
            )

        return report

    async def _run_single(self, sample: SampleSpec, mock: bool = False) -> ResultRecord:
        """执行单条测试"""
        violations: list[str] = []

        # Mock 模式：使用预设结果
        if mock or self._execute_agent is None:
            return await self._mock_run(sample)

        # 真实执行
        try:
            # 捕获 Agent 执行结果
            tool_calls: list[dict[str, Any]] = []
            output_text: str = ""
            token_count: int = 0
            action: str = "execute"

            # 调用 Agent（需要适配 executor 的接口）
            result = await self._execute_agent(sample.input, capture_tools=True)

            if result:
                tool_calls = result.get("tool_calls", [])
                output_text = result.get("output", "")
                token_count = result.get("tokens", 0)
                action = result.get("action", "execute")

            # 提取实际调用的工具名
            actual_tools = [tc.get("name", "") for tc in tool_calls]
            tool_call_count = len(tool_calls)

            # 验证预期行为
            if sample.expected_action and action != sample.expected_action:
                violations.append(f"预期行为 {sample.expected_action}，实际 {action}")

            # 验证必须调用的工具
            for tool in sample.must_call_tools:
                if tool not in actual_tools:
                    violations.append(f"必须调用 {tool} 但未调用")

            # 验证禁止调用的工具
            for tool in sample.must_not_call_tools:
                if tool in actual_tools:
                    violations.append(f"禁止调用 {tool} 但已调用")

            # 验证输出模式
            if sample.expected_output_pattern:
                if not re.search(sample.expected_output_pattern, output_text):
                    violations.append(f"输出不符合模式 {sample.expected_output_pattern}")

            # 验证 token 预算
            if sample.max_tokens and token_count > sample.max_tokens:
                violations.append(f"Token 超限: {token_count} > {sample.max_tokens}")

            # 验证工具调用上限
            if sample.max_tool_calls and tool_call_count > sample.max_tool_calls:
                violations.append(f"工具调用超限: {tool_call_count} > {sample.max_tool_calls}")

            passed = len(violations) == 0

            return ResultRecord(
                sample_name=sample.name,
                passed=passed,
                actual_action=action,
                actual_tools=actual_tools,
                actual_output=output_text,
                token_count=token_count,
                tool_call_count=tool_call_count,
                violations=violations,
                error_message="; ".join(violations) if violations else "",
            )

        except Exception as e:
            return ResultRecord(
                sample_name=sample.name,
                passed=False,
                error_message=str(e),
            )

    async def _mock_run(self, sample: SampleSpec) -> ResultRecord:
        """Mock 执行（用于测试框架本身）

        模拟执行结果并验证约束，用于测试测试框架的正确性。
        """
        # 根据预期行为生成模拟结果
        action = sample.expected_action
        tools = sample.expected_tools[:]

        # 模拟输出
        if sample.expected_action == "reject":
            output = "抱歉，我不能执行这个请求。"
        elif sample.expected_action == "ask_human":
            output = "这个操作需要您的确认。请确认是否继续？"
        else:
            output = f"已处理: {sample.input[:50]}"

        # 验证约束（mock 模式也应该验证）
        violations: list[str] = []

        # 检查 must_call_tools 是否在 expected_tools 中
        for tool in sample.must_call_tools:
            if tool not in sample.expected_tools:
                violations.append(f"Mock 配置错误: must_call_tools 包含 {tool} 但 expected_tools 不包含")

        # 检查 must_not_call_tools 是否在 expected_tools 中（不应该）
        for tool in sample.must_not_call_tools:
            if tool in sample.expected_tools:
                violations.append(f"Mock 配置错误: must_not_call_tools 包含 {tool} 但 expected_tools 也包含")

        # 检查 max_tool_calls 约束
        if sample.max_tool_calls is not None and len(sample.expected_tools) > sample.max_tool_calls:
            violations.append(f"Mock 配置错误: expected_tools 数量 {len(sample.expected_tools)} > max_tool_calls {sample.max_tool_calls}")

        passed = len(violations) == 0

        return ResultRecord(
            sample_name=sample.name,
            passed=passed,
            actual_action=action,
            actual_tools=tools,
            actual_output=output,
            token_count=50,
            tool_call_count=len(tools),
            violations=violations,
            error_message="; ".join(violations) if violations else "",
        )

    def get_last_report(self) -> dict[str, Any] | None:
        """获取最近一次测试报告（保存的）"""
        report_path = Path("workspaces/test_report.json")
        if not report_path.exists():
            return None

        try:
            with open(report_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None


async def run_self_test(
    category: str | None = None,
    name_pattern: str | None = None,
    *,
    samples_dir: str = "tests/evaluation/samples",
    execute_agent: Callable | None = None,
    term_write: Callable | None = None,
    mock: bool = False,
) -> TestReport:
    """便捷函数：运行自测

    Args:
        category: 按类别过滤
        name_pattern: 按名称过滤
        samples_dir: 测试样本目录
        execute_agent: Agent 执行函数
        term_write: 输出函数
        mock: 是否使用 mock 模式

    Returns:
        测试报告
    """
    runner = TestRunner(
        samples_dir=samples_dir,
        execute_agent=execute_agent,
        term_write=term_write,
    )
    report = await runner.run_tests(category, name_pattern, mock=mock)

    # 保存报告
    report_path = Path("workspaces/test_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)

    return report


__all__ = ["TestRunner", "run_self_test"]