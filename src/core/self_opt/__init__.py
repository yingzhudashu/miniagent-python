"""Self-Optimization Subsystem (Phase 9)

自动优化子系统。

核心模块：
- inspector: 自我审视引擎
- proposal_engine: 优化提案生成器
- self_test_runner: 测试执行器
- git_snapshot: Git 快照管理
- diff_generator: 差异生成器
- confirmation_manager: 确认管理器
- optimization_learner: 优化学习器
- researcher: 外部研究引擎
- runtime_error_collector: 运行时错误收集器
- error_analyzer: 错误分析引擎
- structured_logger: 结构化日志
- metrics: 指标仪表板
- auto_optimizer: 自动优化编排器
"""

# Types
from .types import (
    RiskLevel,
    TestCaseType,
    TestCase,
    OptimizationType,
    FileChange,
    OptimizationProposal,
    CodeQualityMetric,
    ModuleAnalysis,
    ArchitectureCheck,
    PainPoint,
    InspectionReport,
    ExternalReference,
    ExtractedPattern,
    ResearchReport,
    OptimizationStatus,
    TestExecutionResult,
    TestSummary,
    OptimizationResult,
    OptimizationLogEntry,
    OptimizationSummary,
)

# Inspector
from .inspector import (
    scan_py_files,
    inspect_project,
)

# Proposal Engine
from .proposal_engine import (
    generate_proposals,
    format_proposals,
    PROPOSAL_TEMPLATES,
)

# Self Test Runner
from .self_test_runner import (
    execute_optimization,
    run_test_case,
    apply_file_changes,
)

# Git Snapshot
from .git_snapshot import (
    create_snapshot,
    revert_to_snapshot,
    finalize_snapshot,
    is_in_git_repo,
    SnapshotInfo,
)

# Diff Generator
from .diff_generator import (
    generate_fix_diff,
    apply_diff,
    FixDiff,
)

# Confirmation Manager
from .confirmation_manager import (
    ConfirmationManager,
    ConfirmationRequest,
)

# Optimization Learner
from .optimization_learner import (
    load_history,
    analyze_history,
    adjust_risk,
    get_disabled_templates,
    LearningInsights,
    TemplateStats,
    save_learning_state,
)

# Researcher
from .researcher import (
    research_topic,
    generate_research_report,
    KNOWN_PATTERNS,
)

# Runtime Error Collector
from .runtime_error_collector import (
    collect_error,
    collect_errors,
    parse_error_log,
    detect_frequent_errors,
    RuntimeErrorRecord,
    ErrorContext,
)

# Error Analyzer
from .error_analyzer import (
    analyze_errors,
    inject_errors_into_inspection,
    ErrorAnalysis,
    ErrorCluster,
)

# Structured Logger
from .structured_logger import (
    log_optimize_start,
    log_optimize_complete,
    log_proposal_executed,
    log_test_run,
    log_fix_attempt,
    log_rollback,
    log_error,
    load_optimization_log,
    StructuredLogEntry,
)

# Metrics
from .metrics import (
    collect_metrics,
    get_dashboard,
    get_trend,
    DashboardData,
    MetricTrend,
    MetricPoint,
)

# Auto Optimizer
from .auto_optimizer import (
    run_auto_optimization,
    AutoOptimizeResult,
)

__all__ = [
    # Types
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
    # Inspector
    "scan_py_files",
    "inspect_project",
    # Proposal Engine
    "generate_proposals",
    "format_proposals",
    "PROPOSAL_TEMPLATES",
    # Self Test Runner
    "execute_optimization",
    "run_test_case",
    "apply_file_changes",
    # Git Snapshot
    "create_snapshot",
    "revert_to_snapshot",
    "finalize_snapshot",
    "is_in_git_repo",
    "SnapshotInfo",
    # Diff Generator
    "generate_fix_diff",
    "apply_diff",
    "FixDiff",
    # Confirmation Manager
    "ConfirmationManager",
    "ConfirmationRequest",
    # Optimization Learner
    "load_history",
    "analyze_history",
    "adjust_risk",
    "get_disabled_templates",
    "LearningInsights",
    "TemplateStats",
    "save_learning_state",
    # Researcher
    "research_topic",
    "generate_research_report",
    "KNOWN_PATTERNS",
    # Runtime Error Collector
    "collect_error",
    "collect_errors",
    "parse_error_log",
    "detect_frequent_errors",
    "RuntimeErrorRecord",
    "ErrorContext",
    # Error Analyzer
    "analyze_errors",
    "inject_errors_into_inspection",
    "ErrorAnalysis",
    "ErrorCluster",
    # Structured Logger
    "log_optimize_start",
    "log_optimize_complete",
    "log_proposal_executed",
    "log_test_run",
    "log_fix_attempt",
    "log_rollback",
    "log_error",
    "load_optimization_log",
    "StructuredLogEntry",
    # Metrics
    "collect_metrics",
    "get_dashboard",
    "get_trend",
    "DashboardData",
    "MetricTrend",
    "MetricPoint",
    # Auto Optimizer
    "run_auto_optimization",
    "AutoOptimizeResult",
]
