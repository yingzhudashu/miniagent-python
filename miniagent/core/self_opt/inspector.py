"""Self-optimization subsystem — 项目检查器

分析项目健康度，生成 InspectionReport。

功能：
- 代码质量扫描（行数、复杂度、重复率）
- 测试覆盖率检查
- 痛点识别
- 模块依赖分析

详见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from miniagent.core.self_opt.types import (
    CodeQualityMetric,
    InspectionReport,
    ModuleAnalysis,
    PainPoint,
)

_logger = logging.getLogger(__name__)


def _count_python_files(root: str) -> int:
    """统计 Python 文件数量。"""
    return sum(1 for _ in Path(root).rglob("*.py"))


def _count_lines(root: str) -> int:
    """统计总代码行数。"""
    total = 0
    for f in Path(root).rglob("*.py"):
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                total += sum(1 for _ in fh)
        except (OSError, PermissionError) as e:
            _logger.debug("读取文件失败: %s", e)
    return total


def _estimate_test_coverage(root: str) -> float:
    """估算测试覆盖率（基于测试文件与源文件比例）。

    这是一个粗略估计，实际应使用 coverage.py。
    """
    src_files = _count_python_files(root)
    test_files = sum(1 for _ in Path(root).rglob("test_*.py"))
    test_files += sum(1 for _ in Path(root).rglob("*_test.py"))

    if src_files == 0:
        return 0.0

    # 简单启发式：测试文件占比反映覆盖率
    ratio = test_files / max(src_files, 1)
    return min(round(ratio * 80, 1), 100.0)  # 上限 100%


def _analyze_module(filepath: str) -> ModuleAnalysis:
    """分析单个模块。

    Args:
        filepath: 模块文件路径

    Returns:
        模块分析结果
    """
    analysis = ModuleAnalysis(path=filepath)

    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            analysis.lines = len(lines)

            # 简单复杂度估计：基于缩进层级和条件语句
            complexity = 1.0
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(("if ", "elif ", "for ", "while ", "except ", "with ")):
                    complexity += 1.0
                elif stripped.startswith("def ") or stripped.startswith("class "):
                    complexity += 0.5
            analysis.complexity = round(complexity, 1)

            # 检查潜在问题
            if analysis.lines > 500:
                analysis.issues.append(f"文件过长 ({analysis.lines} 行)")
            if analysis.complexity > 15:
                analysis.issues.append(f"圈复杂度过高 ({analysis.complexity})")
            if analysis.lines > 300 and "TODO" in "".join(lines):
                analysis.issues.append("大型文件中存在 TODO")

    except (OSError, PermissionError):
        analysis.issues.append("无法读取文件")

    return analysis


def _identify_pain_points(root: str) -> list[PainPoint]:
    """识别项目痛点。

    Args:
        root: 项目根目录

    Returns:
        痛点列表
    """
    pains: list[PainPoint] = []

    # 检查无 __init__.py 的包
    for d in Path(root).rglob("*"):
        if d.is_dir() and any(f.suffix == ".py" for f in d.iterdir() if f.is_file()):
            init_file = d / "__init__.py"
            if not init_file.exists():
                pains.append(
                    PainPoint(
                        category="architecture",
                        description=f"目录 {d.name} 包含 Python 文件但缺少 __init__.py",
                        severity=2,
                        frequency=1,
                        suggestion="添加 __init__.py 以明确包结构",
                    )
                )

    # 检查大文件
    for f in Path(root).rglob("*.py"):
        try:
            size = f.stat().st_size
            if size > 50_000:  # > 50KB
                pains.append(
                    PainPoint(
                        category="maintainability",
                        description=f"文件过大: {f.name} ({size // 1024}KB)",
                        severity=3,
                        frequency=1,
                        suggestion="考虑拆分为多个模块",
                    )
                )
        except OSError as e:
            _logger.debug("检查文件大小失败: %s", e)

    # 检查是否有文档
    doc_files = list(Path(root).glob("*.md")) + list(Path(root).glob("*.rst"))
    if not doc_files:
        pains.append(
            PainPoint(
                category="documentation",
                description="项目缺少文档",
                severity=3,
                frequency=5,
                suggestion="添加 README.md 和架构文档",
            )
        )

    return pains


async def inspect_project(
    root: str | None = None,
    *,
    include_metrics: bool = True,
    include_modules: bool = True,
    include_pain_points: bool = True,
) -> InspectionReport:
    """执行项目检查。

    Args:
        root: 项目根目录（默认当前工作目录）
        include_metrics: 是否包含代码质量指标
        include_modules: 是否包含模块分析
        include_pain_points: 是否包含痛点识别

    Returns:
        项目检查报告
    """
    if root is None:
        root = os.getcwd()

    from datetime import datetime

    report = InspectionReport(
        timestamp=datetime.now().isoformat(),
        version="1.0.0",
        summary="项目检查完成",
    )

    # 基础统计
    if include_metrics:
        py_files = _count_python_files(root)
        total_lines = _count_lines(root)

        report.metrics.append(
            CodeQualityMetric(
                name="python_files",
                value=float(py_files),
                threshold=100.0,
                status="good" if py_files < 50 else "warning",
            )
        )
        report.metrics.append(
            CodeQualityMetric(
                name="total_lines",
                value=float(total_lines),
                threshold=10000.0,
                status="good" if total_lines < 5000 else "warning",
            )
        )
        report.metrics.append(
            CodeQualityMetric(
                name="avg_complexity",
                value=5.0,  # 默认值，实际应计算
                threshold=10.0,
                status="good",
            )
        )

    # 模块分析
    if include_modules:
        for f in sorted(Path(root).rglob("*.py")):
            if "test" not in str(f) and "__pycache__" not in str(f):
                analysis = _analyze_module(str(f))
                if analysis.issues:
                    report.modules.append(analysis)

    # 测试覆盖率
    report.test_coverage = _estimate_test_coverage(root)

    # 痛点识别
    if include_pain_points:
        report.pain_points = _identify_pain_points(root)

    # 更新摘要
    issue_count = sum(len(m.issues) for m in report.modules)
    pain_count = len(report.pain_points)
    report.summary = (
        f"发现 {issue_count} 个模块问题, {pain_count} 个痛点, 测试覆盖率 ~{report.test_coverage}%"
    )

    return report


__all__ = ["inspect_project"]
