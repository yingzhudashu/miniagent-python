"""Metrics Dashboard — 指标仪表板

收集和展示 Self-Optimization 子系统的各项指标。

核心指标：
- 优化成功率、失败率
- 平均优化耗时
- 高频错误类型
- 架构健康度
- 测试覆盖率趋势

设计原则：
- 数据来自多个源（inspector, error_analyzer, learner）
- 支持时间趋势分析
- 格式化输出为可读仪表板
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .types import InspectionReport, OptimizationResult, OptimizationSummary
from .structured_logger import load_optimization_log, read_raw_structured_log, filter_structured_log


@dataclass
class MetricPoint:
    """单个指标数据点。"""
    timestamp: str
    value: float
    label: str = ""


@dataclass
class MetricTrend:
    """指标趋势。"""
    name: str
    current: float
    previous: float
    change: float  # 百分比变化
    points: list[MetricPoint] = field(default_factory=list)


@dataclass
class DashboardData:
    """仪表板数据。"""
    timestamp: str
    summary: OptimizationSummary | None = None
    health_score: float = 0.0
    success_rate: float = 0.0
    avg_duration_seconds: float = 0.0
    total_proposals: int = 0
    successful_proposals: int = 0
    failed_proposals: int = 0
    reverted_proposals: int = 0
    error_count: int = 0
    test_coverage: str = "0%"
    file_count: int = 0
    lines_of_code: int = 0
    active_pain_points: int = 0
    disabled_templates: list[str] = field(default_factory=list)


def _calculate_success_rate(results: list[OptimizationResult]) -> float:
    """计算成功率。"""
    if not results:
        return 0.0
    success = sum(1 for r in results if r.status == "success")
    return round(success / len(results) * 100, 1)


def _calculate_avg_duration(results: list[OptimizationResult]) -> float:
    """计算平均耗时。"""
    durations = [r.total_duration_seconds for r in results if r.total_duration_seconds > 0]
    if not durations:
        return 0.0
    return round(sum(durations) / len(durations), 1)


async def collect_metrics(
    project_root: str,
    inspection: InspectionReport | None = None,
) -> DashboardData:
    """收集所有指标数据。

    Args:
        project_root: 项目根目录。
        inspection: 可选的自检报告（用于架构指标）。

    Returns:
        仪表板数据。
    """
    import datetime

    # 加载优化历史
    results = load_optimization_log(project_root)
    optimization_results = [r.result for r in results]

    # 计算基本指标
    total = len(optimization_results)
    success = sum(1 for r in optimization_results if r.status == "success")
    failed = sum(1 for r in optimization_results if r.status == "failed")
    reverted = sum(1 for r in optimization_results if r.reverted)

    # 从自检报告获取架构指标
    health_score = 0.0
    test_coverage = "0%"
    file_count = 0
    loc = 0
    active_pain_points = 0

    if inspection:
        # 架构健康度
        passed = sum(1 for c in inspection.architecture_checks if c.passed)
        total_checks = len(inspection.architecture_checks)
        health_score = round((passed / max(total_checks, 1)) * 100, 1)

        # 测试覆盖率
        for metric in inspection.quality_metrics:
            if "测试覆盖" in metric.name or "test" in metric.name.lower():
                test_coverage = str(metric.value) if isinstance(metric.value, str) else f"{metric.value}%"
                break

        # 文件数和行数
        for metric in inspection.quality_metrics:
            if "文件" in metric.name or "file" in metric.name.lower():
                file_count = int(float(metric.value)) if isinstance(metric.value, (int, float)) else 0
            if "代码行" in metric.name or "line" in metric.name.lower():
                loc = int(float(metric.value)) if isinstance(metric.value, (int, float)) else 0

        active_pain_points = len(inspection.pain_points)

    dashboard = DashboardData(
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        summary=OptimizationSummary(
            total_optimizations=total,
            successful=success,
            failed=failed,
            reverted=reverted,
            last_optimization=optimization_results[-1].timestamp if optimization_results else None,
        ),
        health_score=health_score,
        success_rate=_calculate_success_rate(optimization_results),
        avg_duration_seconds=_calculate_avg_duration(optimization_results),
        total_proposals=total,
        successful_proposals=success,
        failed_proposals=failed,
        reverted_proposals=reverted,
        test_coverage=test_coverage,
        file_count=file_count,
        lines_of_code=loc,
        active_pain_points=active_pain_points,
    )

    return dashboard


def get_dashboard(
    dashboard: DashboardData,
    format: str = "text",
) -> str:
    """格式化仪表板数据为可读输出。

    Args:
        dashboard: 仪表板数据。
        format: 输出格式 ("text" | "markdown")。

    Returns:
        格式化后的仪表板字符串。
    """
    if format == "markdown":
        return _format_markdown(dashboard)
    return _format_text(dashboard)


def _format_text(d: DashboardData) -> str:
    """文本格式。"""
    lines = [
        "=" * 50,
        "  Self-Optimization Dashboard",
        "=" * 50,
        f"  架构健康度:    {d.health_score}%",
        f"  优化成功率:    {d.success_rate}%",
        f"  平均优化耗时:  {d.avg_duration_seconds}s",
        f"  总提案数:      {d.total_proposals}",
        f"  成功:          {d.successful_proposals}",
        f"  失败:          {d.failed_proposals}",
        f"  回滚:          {d.reverted_proposals}",
        f"  运行时错误:    {d.error_count}",
        f"  测试覆盖率:    {d.test_coverage}",
        f"  文件数:        {d.file_count}",
        f"  代码行数:      {d.lines_of_code}",
        f"  活跃痛点:      {d.active_pain_points}",
        "=" * 50,
    ]
    return "\n".join(lines)


def _format_markdown(d: DashboardData) -> str:
    """Markdown 格式。"""
    lines = [
        "# 📊 Self-Optimization Dashboard",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| 架构健康度 | {d.health_score}% |",
        f"| 优化成功率 | {d.success_rate}% |",
        f"| 平均优化耗时 | {d.avg_duration_seconds}s |",
        f"| 总提案数 | {d.total_proposals} |",
        f"| 成功 | {d.successful_proposals} |",
        f"| 失败 | {d.failed_proposals} |",
        f"| 回滚 | {d.reverted_proposals} |",
        f"| 运行时错误 | {d.error_count} |",
        f"| 测试覆盖率 | {d.test_coverage} |",
        f"| 文件数 | {d.file_count} |",
        f"| 代码行数 | {d.lines_of_code} |",
        f"| 活跃痛点 | {d.active_pain_points} |",
    ]
    if d.disabled_templates:
        lines.append("")
        lines.append("**Disabled Templates:**")
        for t in d.disabled_templates:
            lines.append(f"- `{t}`")
    return "\n".join(lines)


def get_trend(
    project_root: str,
    metric_name: str = "success_rate",
    window_days: int = 30,
) -> MetricTrend:
    """获取指标趋势。

    Args:
        project_root: 项目根目录。
        metric_name: 指标名称。
        window_days: 时间窗口（天）。

    Returns:
        指标趋势。
    """
    import datetime

    results = load_optimization_log(project_root)
    optimization_results = [r.result for r in results]

    if not optimization_results:
        return MetricTrend(name=metric_name, current=0.0, previous=0.0, change=0.0)

    # 按时间窗口分组
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(days=window_days)

    recent = []
    older = []
    for r in optimization_results:
        try:
            ts = datetime.datetime.fromisoformat(r.timestamp.replace("Z", "+00:00"))
            if ts >= cutoff:
                recent.append(r)
            else:
                older.append(r)
        except Exception:
            pass

    current_rate = _calculate_success_rate(recent) if recent else 0.0
    previous_rate = _calculate_success_rate(older) if older else 0.0

    change = current_rate - previous_rate

    # 构建数据点
    points: list[MetricPoint] = []
    for r in optimization_results:
        points.append(MetricPoint(
            timestamp=r.timestamp,
            value=1.0 if r.status == "success" else 0.0,
            label=r.proposal_id,
        ))

    return MetricTrend(
        name=metric_name,
        current=current_rate,
        previous=previous_rate,
        change=round(change, 1),
        points=points,
    )
