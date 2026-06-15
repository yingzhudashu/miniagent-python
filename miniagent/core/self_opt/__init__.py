"""自我优化子系统（编程 API，不通过工具暴露给 Agent）

- ``inspector``：代码与结构静态分析
- ``proposal_engine``：生成可读的优化提案
- ``git_snapshot``：变更前后 Git 快照
- ``auto_optimizer``：在约束下尝试低风险变更（仅编程 API 使用）
- ``proposal_store``：提案持久化存储与状态管理
- ``runtime_analyzer``：运行日志分析（从 trace/activity_log 提取指标）
- ``proposal_generator``：运行日志驱动的提案生成

本子系统不再作为 Agent 工具注册（``miniagent/tools/self_opt.py`` 已移除）。
Agent 若需自优化能力，可由调用方自行封装工具。

配置开关见 ``config.defaults.json``（self_optimization 配置节）。
类型模型见下方导出；用户文档见 ``docs/SELF_OPT.md``。"""

from miniagent.core.self_opt.auto_optimizer import apply_proposal, run_auto_optimization
from miniagent.core.self_opt.inspector import inspect_project
from miniagent.core.self_opt.proposal_engine import generate_proposals
from miniagent.core.self_opt.proposal_generator import ProposalGenerator
from miniagent.core.self_opt.proposal_store import (
    ProposalStore,
    get_history_file,
    get_proposal_file,
    get_proposal_output_dir,
    get_reports_dir,
)
from miniagent.core.self_opt.runtime_analyzer import RuntimeAnalyzer
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
    # 类型模型
    "OptTestCase",
    "FileChange",
    "OptimizationProposal",
    "CodeQualityMetric",
    "ModuleAnalysis",
    "PainPoint",
    "InspectionReport",
    "OptimizationResult",
    "OptTestSummary",
    # 分析与提案
    "inspect_project",
    "generate_proposals",
    "apply_proposal",
    "run_auto_optimization",
    # 提案存储
    "ProposalStore",
    "get_proposal_output_dir",
    "get_proposal_file",
    "get_history_file",
    "get_reports_dir",
    # 运行分析
    "RuntimeAnalyzer",
    "ProposalGenerator",
]
