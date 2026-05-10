"""Self-optimization subsystem.

提供 Agent 自我优化能力：
- 项目检查（inspector）：分析代码质量、测试覆盖、性能瓶颈
- 提案生成（proposal_engine）：基于检查结果生成优化建议
- 自动优化（auto_optimizer）：在安全约束下自动实施低风险变更
- Git 快照（git_snapshot）：变更前后版本控制
"""

from miniagent.core.self_opt.types import (
    OptTestCase,
    FileChange,
    OptimizationProposal,
    CodeQualityMetric,
    ModuleAnalysis,
    PainPoint,
    InspectionReport,
    OptimizationResult,
    OptTestSummary,
)

__all__ = [
    "OptTestCase",
    "FileChange",
    "OptimizationProposal",
    "CodeQualityMetric",
    "ModuleAnalysis",
    "PainPoint",
    "InspectionReport",
    "OptimizationResult",
    "OptTestSummary",
]
