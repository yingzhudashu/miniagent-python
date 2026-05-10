"""自我优化子系统（工具可调用的内省与提案管线）

- ``inspector``：代码与结构静态分析
- ``proposal_engine``：生成可读的优化提案
- ``auto_optimizer``：在约束下尝试低风险变更
- ``git_snapshot``：变更前后快照

对外工具由 ``miniagent.tools.self_opt`` 注册；类型模型见下方导出。"""

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
