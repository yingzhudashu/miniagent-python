"""自我优化子系统（编程 API，不通过工具暴露给 Agent）

- ``inspector``：代码与结构静态分析
- ``proposal_engine``：生成可读的优化提案
- ``git_snapshot``：变更前后 Git 快照
- ``auto_optimizer``：在约束下尝试低风险变更（仅编程 API 使用）

本子系统不再作为 Agent 工具注册（``miniagent/tools/self_opt.py`` 已移除）。
Agent 若需自优化能力，可由调用方自行封装工具。

类型模型见下方导出；用户文档与安全开关（``MINIAGENT_SELF_OPT_TOOLS``）见 ``docs/SELF_OPT.md``。"""

from miniagent.core.self_opt.types import (
    CodeQualityMetric,
    FileChange,
    InspectionReport,
    ModuleAnalysis,
    OptimizationProposal,
    OptimizationResult,
    OptTestCase,
    OptTestSummary,
    PainPoint,
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
