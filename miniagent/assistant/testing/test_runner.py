"""Mini Agent Python — 自测执行器

执行测试样本并生成报告。

**运行模式**：

- ``mock=True``（默认）：校验样本 JSON 自洽，并用理想化模拟输出验证约束是否可满足。
  不调用 LLM，适合 CI 与 ``/test run`` 快速检查。
- ``mock=False`` 且注入 ``execute_agent``：调用真实 Agent，全面评估行为。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.assistant.infrastructure.persistence import dump_state_file, load_state_file
from miniagent.assistant.infrastructure.state_schemas import install_builtin_state_schemas
from miniagent.assistant.testing.types import (
    DEFAULT_REPORT_PATH,
    DEFAULT_SAMPLES_DIR,
    ExecuteAgentFn,
    ReportSummary,
    ResultRecord,
    SampleSpec,
)
from miniagent.assistant.testing.validation import (
    evaluate_sample_result,
    generate_mock_output,
    mock_tools_for_sample,
    validate_sample_schema,
)

_logger = get_logger(__name__)
install_builtin_state_schemas()

TermWriteFn = Callable[[str, str], None]


class TestRunner:
    """测试执行器

    加载测试样本、执行测试并生成报告。

    Example:
        runner = TestRunner(samples_dir="tests/evaluation/samples")
        report = await runner.run_tests(category="security", mock=True)
        report = await runner.run_tests(mock=False, execute_agent=my_fn)
    """

    def __init__(
        self,
        samples_dir: str = DEFAULT_SAMPLES_DIR,
        *,
        report_path: str = DEFAULT_REPORT_PATH,
        execute_agent: ExecuteAgentFn | None = None,
        term_write: TermWriteFn | None = None,
    ) -> None:
        """创建测试执行器

        Args:
            samples_dir: 测试样本目录
            report_path: 报告 JSON 写入路径
            execute_agent: 真实 Agent 执行函数（``mock=False`` 时必填）
            term_write: CLI 输出 ``(text, ansi_color) -> None``
        """
        self._samples_dir = Path(samples_dir)
        self._report_path = Path(report_path)
        self._execute_agent = execute_agent
        self._term_write = term_write
        self._samples: list[SampleSpec] = []
        self._loaded = False
        self._last_report: ReportSummary | None = None

    def load_samples(
        self,
        category: str | None = None,
        name_pattern: str | None = None,
    ) -> list[SampleSpec]:
        """加载测试样本

        Args:
            category: 按类别过滤（None 表示全部）
            name_pattern: 按名称过滤（正则）

        Returns:
            匹配的测试样本列表（按 priority 升序）
        """
        self._samples = []

        if not self._samples_dir.exists():
            _logger.warning("测试样本目录不存在: %s", self._samples_dir)
            return []

        for json_file in self._samples_dir.rglob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        _logger.warning("跳过非对象样本: %s", json_file)
                        continue
                    sample = SampleSpec.from_dict(item)
                    schema_errors = validate_sample_schema(sample)
                    if schema_errors:
                        _logger.warning(
                            "样本 %s (%s) 字段校验失败: %s",
                            sample.name or json_file,
                            json_file,
                            "; ".join(schema_errors),
                        )
                    self._samples.append(sample)

            except Exception as e:
                _logger.error("加载测试样本失败: %s (%s)", json_file, e)

        filtered: list[SampleSpec] = []
        name_re = re.compile(name_pattern) if name_pattern else None

        for sample in self._samples:
            if category and sample.category != category:
                continue
            if name_re and not name_re.search(sample.name):
                continue
            filtered.append(sample)

        filtered.sort(key=lambda s: s.priority)
        self._loaded = True
        return filtered

    def list_samples(self) -> list[dict[str, Any]]:
        """列出所有已加载测试样本（摘要形式）"""
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
        save_report: bool = False,
    ) -> ReportSummary:
        """运行测试

        Args:
            category: 按类别过滤
            name_pattern: 按名称过滤
            mock: True = 样本 lint + 模拟评估；False = 真实 Agent（需 ``execute_agent``）
            save_report: 是否写入 ``report_path``

        Returns:
            测试报告
        """
        samples = self.load_samples(category, name_pattern)
        report = ReportSummary(total=len(samples))

        if mock is False and self._execute_agent is None:
            raise ValueError("真实模式需要注入 execute_agent，或使用 mock=True")

        mode_label = "mock（样本校验）" if mock else "real（真实 Agent）"
        if self._term_write:
            self._term_write(f"\n[cyan]开始运行 {len(samples)} 条测试 [{mode_label}]...[/cyan]\n", "ansicyan")

        start_time = time.time()

        for sample in samples:
            try:
                result = await self._run_single(sample, mock=mock)
                report.results.append(result)

                if result.passed:
                    report.passed += 1
                    if self._term_write:
                        self._term_write(f"[green]✓[/green] {sample.name}\n", "ansigreen")
                elif result.error_message.startswith("跳过:"):
                    report.skipped += 1
                    if self._term_write:
                        self._term_write(f"[yellow]⊘[/yellow] {sample.name}: {result.error_message}\n", "ansiyellow")
                else:
                    report.failed += 1
                    if self._term_write:
                        self._term_write(
                            f"[red]✗[/red] {sample.name}: {result.error_message}\n",
                            "ansired",
                        )

            except Exception as e:
                report.skipped += 1
                report.results.append(
                    ResultRecord(
                        sample_name=sample.name,
                        passed=False,
                        error_message=f"跳过: {e}",
                    )
                )
                if self._term_write:
                    self._term_write(f"[yellow]⊗[/yellow] {sample.name}: {e}\n", "ansiyellow")

        report.duration_seconds = time.time() - start_time
        self._last_report = report

        if self._term_write:
            self._term_write(
                f"\n[cyan]测试完成: {report.passed}/{report.total} 通过, "
                f"{report.failed} 失败, {report.skipped} 跳过 "
                f"({report.pass_rate:.1%}, {report.duration_seconds:.1f}s)[/cyan]\n",
                "ansicyan",
            )

        if save_report:
            self.save_report(report)

        return report

    def save_report(self, report: ReportSummary | None = None) -> Path:
        """将报告写入 ``report_path``。"""
        data = (report or self._last_report)
        if data is None:
            raise ValueError("无报告可保存")

        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        dump_state_file("testing_report", self._report_path, data.to_dict())
        return self._report_path

    async def _run_single(self, sample: SampleSpec, mock: bool = False) -> ResultRecord:
        """执行单条测试"""
        if mock or self._execute_agent is None:
            return self._run_mock_evaluation(sample)

        try:
            result = await self._execute_agent(sample.input, capture_tools=True)
            tool_calls = result.get("tool_calls", [])
            output_text = result.get("output", "")
            token_count = int(result.get("tokens", 0))
            action = result.get("action", "execute")
            actual_tools = [tc.get("name", "") for tc in tool_calls if tc.get("name")]
            tool_call_count = len(tool_calls)

            violations = evaluate_sample_result(
                sample,
                actual_action=action,
                actual_tools=actual_tools,
                output_text=output_text,
                token_count=token_count,
                tool_call_count=tool_call_count,
            )

            return ResultRecord(
                sample_name=sample.name,
                passed=len(violations) == 0,
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

    def _run_mock_evaluation(self, sample: SampleSpec) -> ResultRecord:
        """mock 模式：校验样本定义，并用理想化输出验证约束是否可满足。"""
        violations = validate_sample_schema(sample)

        action = sample.expected_action
        actual_tools = mock_tools_for_sample(sample)
        output_text = generate_mock_output(sample)
        tool_call_count = len(actual_tools)
        token_count = max(1, len(output_text) // 4 + tool_call_count * 50)

        violations.extend(
            evaluate_sample_result(
                sample,
                actual_action=action,
                actual_tools=actual_tools,
                output_text=output_text,
                token_count=token_count,
                tool_call_count=tool_call_count,
            )
        )

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

    def get_last_report(self) -> dict[str, Any] | None:
        """获取最近一次测试报告（内存优先，其次读 ``report_path``）。"""
        if self._last_report is not None:
            return self._last_report.to_dict()

        if not self._report_path.exists():
            return None

        try:
            return load_state_file("testing_report", self._report_path)
        except Exception:
            return None


async def run_self_test(
    category: str | None = None,
    name_pattern: str | None = None,
    *,
    samples_dir: str = DEFAULT_SAMPLES_DIR,
    report_path: str = DEFAULT_REPORT_PATH,
    execute_agent: ExecuteAgentFn | None = None,
    term_write: TermWriteFn | None = None,
    mock: bool = True,
) -> ReportSummary:
    """便捷函数：运行自测并保存报告

    Args:
        category: 按类别过滤
        name_pattern: 按名称过滤
        samples_dir: 测试样本目录
        report_path: 报告输出路径
        execute_agent: 真实 Agent 执行函数（``mock=False`` 时必填）
        term_write: CLI 输出 ``(text, color) -> None``
        mock: True = 不调用 LLM，仅校验样本与约束自洽；False = 真实评估

    Returns:
        测试报告
    """
    runner = TestRunner(
        samples_dir=samples_dir,
        report_path=report_path,
        execute_agent=execute_agent,
        term_write=term_write,
    )
    return await runner.run_tests(category, name_pattern, mock=mock, save_report=True)


__all__ = ["TestRunner", "run_self_test"]
