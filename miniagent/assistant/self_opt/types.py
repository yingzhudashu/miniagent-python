"""Self-optimization subsystem — 类型定义

定义 Agent 自我优化所需的所有数据结构：
- 检查报告（InspectionReport）：项目健康度快照
- 优化提案（OptimizationProposal）：具体的改进建议
- 文件变更（FileChange）：对源码的修改描述
- 测试用例（OptTestCase）：验证优化效果的测试
- 优化结果（OptimizationResult）：执行优化后的结果

概念说明见 ``docs/SELF_OPT.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ─── 检查相关类型 ─────────────────────────────────────────


@dataclass
class CodeQualityMetric:
    """代码质量指标。

    Attributes:
        name: 指标名称（如 "cyclomatic_complexity", "test_coverage"）
        value: 当前值
        threshold: 警告阈值（超过此值认为需要优化）
        status: 当前状态
    """

    name: str
    value: float
    threshold: float
    status: Literal["good", "warning", "critical"] = "good"


@dataclass
class ModuleAnalysis:
    """模块级分析结果。

    Attributes:
        path: 模块文件路径
        lines: 总行数
        complexity: 圈复杂度
        issues: 发现的问题列表
    """

    path: str
    lines: int = 0
    complexity: float = 0.0
    issues: list[str] = field(default_factory=list)


@dataclass
class PainPoint:
    """项目痛点。

    Attributes:
        category: 痛点类别（如 "architecture", "performance", "testing"）
        description: 痛点描述
        severity: 严重程度 (1-5)
        frequency: 出现频率 (1-5)
        suggestion: 改进建议
    """

    category: str
    description: str
    severity: int = 1
    frequency: int = 1
    suggestion: str = ""


@dataclass
class InspectionReport:
    """项目检查报告。

    由 inspector 生成，描述项目当前健康状态。

    Attributes:
        timestamp: 检查时间戳
        version: 项目版本
        summary: 总体摘要
        metrics: 代码质量指标列表
        modules: 模块分析列表
        pain_points: 发现的痛点
        test_coverage: 测试覆盖率（0-100）
    """

    timestamp: str
    version: str
    summary: str = ""
    metrics: list[CodeQualityMetric] = field(default_factory=list)
    modules: list[ModuleAnalysis] = field(default_factory=list)
    pain_points: list[PainPoint] = field(default_factory=list)
    test_coverage: float = 0.0


# ─── 优化相关类型 ─────────────────────────────────────────


@dataclass
class FileChange:
    """文件变更描述。

    用于描述对源码的一次修改。

    Attributes:
        path: 目标文件路径
        action: 操作类型（create/update/delete/rename）
        content: 新内容（create/update 时使用）
        old_path: 旧路径（rename 时使用）
        reason: 变更原因
    """

    path: str
    action: Literal["create", "update", "delete", "rename"] = "update"
    content: str = ""
    old_path: str = ""
    reason: str = ""


@dataclass
class OptTestCase:
    """优化测试用例。

    用于验证优化是否有效。

    Attributes:
        id: 测试用例 ID
        type: 测试类型（unit/integration/e2e）
        description: 测试描述
        setup: 前置条件
        action: 执行动作
        expected: 预期结果
        command: 执行命令
    """

    id: str
    type: Literal["unit", "integration", "e2e"] = "unit"
    description: str = ""
    setup: str = ""
    action: str = ""
    expected: str = ""
    command: str = ""


@dataclass
class OptimizationProposal:
    """优化提案。

    由 proposal_engine 生成，描述一个具体的优化方案。

    Attributes:
        id: 提案 ID
        type: 提案类型（add/remove/refactor/optimize）
        risk_level: 风险等级（low/medium/high）
        target: 目标模块/文件
        description: 提案描述
        rationale: 提案依据
        expected_benefit: 预期收益
        files: 需要修改的文件列表
        test_cases: 验证测试用例
        estimated_effort: 预估工作量（分钟）
    """

    id: str
    type: Literal["add", "remove", "refactor", "optimize"] = "optimize"
    risk_level: Literal["low", "medium", "high"] = "low"
    target: str = ""
    description: str = ""
    rationale: str = ""
    expected_benefit: str = ""
    files: list[FileChange] = field(default_factory=list)
    test_cases: list[OptTestCase] = field(default_factory=list)
    estimated_effort: int = 0


# ─── 结果相关类型 ─────────────────────────────────────────


@dataclass
class OptTestSummary:
    """优化测试摘要。

    Attributes:
        total: 总测试数
        passed: 通过数
        failed: 失败数
    """

    total: int = 0
    passed: int = 0
    failed: int = 0


@dataclass
class OptimizationResult:
    """优化执行结果。

    Attributes:
        proposal_id: 关联的提案 ID
        status: 执行状态（success/failed/skipped）
        test_summary: 测试摘要
        error: 错误信息（失败时）
        changes_applied: 实际应用的变更数
    """

    proposal_id: str
    status: Literal["success", "failed", "skipped"] = "success"
    test_summary: OptTestSummary | None = None
    error: str = ""
    changes_applied: int = 0


__all__ = [
    "CodeQualityMetric",
    "ModuleAnalysis",
    "PainPoint",
    "InspectionReport",
    "FileChange",
    "OptTestCase",
    "OptimizationProposal",
    "OptTestSummary",
    "OptimizationResult",
]
