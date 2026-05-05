"""Self-Optimization 类型定义 (Phase 9)

Self-Optimization 子系统的所有类型。

类型分类：
1. OptimizationProposal — 优化提案
2. TestCase — 测试用例
3. OptimizationResult — 优化结果
4. InspectionReport — 自我审视报告
5. ResearchReport — 外部调研报告
6. OptimizationLog — 优化历史记录
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Risk Level — 风险等级
# ============================================================================

RiskLevel = str  # "low" | "medium" | "high" | "destructive"

# ============================================================================
# TestCase — 测试用例
# ============================================================================

TestCaseType = str  # "unit" | "integration" | "e2e"


@dataclass
class TestCase:
    """测试用例 — 每个优化提案必须附带至少一个测试用例。"""

    id: str
    type: TestCaseType
    description: str
    setup: str
    action: str
    expected: str
    command: str
    test_file_path: str | None = None


# ============================================================================
# OptimizationProposal — 优化提案
# ============================================================================

OptimizationType = str  # "add" | "remove" | "modify" | "refactor"


@dataclass
class FileChange:
    """文件变更描述。"""

    path: str
    action: str  # "create" | "modify" | "delete"
    content: str | None = None
    description: str | None = None


@dataclass
class OptimizationProposal:
    """优化提案 — 由 ProposalEngine 生成，包含完整的改动计划和验证方案。"""

    id: str
    type: OptimizationType
    risk_level: RiskLevel
    target: str
    description: str
    rationale: str
    expected_benefit: str
    files: list[FileChange] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    test_cases: list[TestCase] = field(default_factory=list)
    rollback_plan: str | None = None
    estimated_time_seconds: int | None = None


# ============================================================================
# InspectionReport — 自我审视报告
# ============================================================================


@dataclass
class CodeQualityMetric:
    """代码质量指标。"""

    name: str
    value: float | str
    target: str | None = None
    passed: bool = True
    note: str | None = None


@dataclass
class ModuleAnalysis:
    """模块分析结果。"""

    path: str
    lines_of_code: int
    has_tests: bool
    exports_count: int
    imports_count: int
    complexity_score: int
    issues: list[str] = field(default_factory=list)


@dataclass
class ArchitectureCheck:
    """架构完整性检查项。"""

    name: str
    passed: bool
    details: str
    recommendation: str | None = None


@dataclass
class PainPoint:
    """痛点项。"""

    description: str
    severity: str  # "low" | "medium" | "high"
    evidence: str


@dataclass
class InspectionReport:
    """自我审视报告 — 由 Inspector 生成。"""

    timestamp: str
    version: str
    quality_metrics: list[CodeQualityMetric] = field(default_factory=list)
    module_analysis: list[ModuleAnalysis] = field(default_factory=list)
    architecture_checks: list[ArchitectureCheck] = field(default_factory=list)
    pain_points: list[PainPoint] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    summary: str = ""


# ============================================================================
# ResearchReport — 外部调研报告
# ============================================================================


@dataclass
class ExternalReference:
    """外部架构/项目信息。"""

    type: str  # "paper" | "github" | "blog" | "docs"
    title: str
    url: str
    summary: str
    date: str | None = None
    patterns: list[str] = field(default_factory=list)
    relevance: int = 5


@dataclass
class ExtractedPattern:
    """提取的架构模式。"""

    name: str
    description: str
    source_references: list[str] = field(default_factory=list)
    applicability: str = ""


@dataclass
class ResearchReport:
    """外部调研报告 — 由 Researcher 生成。"""

    timestamp: str
    search_queries: list[str] = field(default_factory=list)
    references: list[ExternalReference] = field(default_factory=list)
    extracted_patterns: list[ExtractedPattern] = field(default_factory=list)
    summary: str = ""


# ============================================================================
# OptimizationResult — 优化结果
# ============================================================================

OptimizationStatus = str  # "success" | "failed" | "reverted" | "skipped"


@dataclass
class TestExecutionResult:
    """单个测试执行结果。"""

    test_case_id: str
    passed: bool
    output: str
    duration_ms: float


@dataclass
class TestSummary:
    """测试汇总。"""

    total: int
    passed: int
    failed: int


@dataclass
class OptimizationResult:
    """优化执行结果。"""

    proposal_id: str
    status: OptimizationStatus
    test_results: list[TestExecutionResult] = field(default_factory=list)
    test_summary: TestSummary | None = None
    git_snapshot: str | None = None
    fix_attempts: int = 0
    reverted: bool = False
    lesson: str = ""
    timestamp: str = ""
    total_duration_seconds: float = 0.0


# ============================================================================
# OptimizationLog — 优化历史记录
# ============================================================================


@dataclass
class OptimizationLogEntry:
    """优化历史记录条目。"""

    result: OptimizationResult
    proposal_id: str
    proposal_type: OptimizationType
    proposal_target: str
    proposal_description: str
    proposal_risk_level: RiskLevel


@dataclass
class OptimizationSummary:
    """优化历史汇总。"""

    total_optimizations: int
    successful: int
    failed: int
    reverted: int
    last_optimization: str | None = None
    top_pain_points_resolved: list[str] = field(default_factory=list)


__all__ = [
    "RiskLevel",
    "TestCaseType",
    "TestCase",
    "OptimizationType",
    "FileChange",
    "OptimizationProposal",
    "CodeQualityMetric",
    "ModuleAnalysis",
    "ArchitectureCheck",
    "PainPoint",
    "InspectionReport",
    "ExternalReference",
    "ExtractedPattern",
    "ResearchReport",
    "OptimizationStatus",
    "TestExecutionResult",
    "TestSummary",
    "OptimizationResult",
    "OptimizationLogEntry",
    "OptimizationSummary",
]
