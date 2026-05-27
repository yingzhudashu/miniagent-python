# Self-Opt 自优化文档

> Mini Agent Python | 版本: 2.0.2 | Self-Optimization 子系统

> ⚠️ **注意**：`miniagent/tools/self_opt.py` 已移除，self-opt **不再作为 Agent 工具**暴露。
> `miniagent/core/self_opt/` 库仍可作为编程 API 使用（见下方类型模型），但 Agent 执行阶段无法通过工具调用触发。

## 概述

Self-Opt 是 Mini Agent 的自我优化机制，通过对项目代码进行静态分析和结构检查，识别质量问题和痛点，生成可读的优化提案，并在约束下安全地应用变更。

> **不是对话日志分析器。** 本子系统面向**代码质量**（而非运行时对话），与「记忆层」和「活动日志」正交。

## 模块结构

```
miniagent/core/self_opt/
├── __init__.py             # 包入口，导出类型模型
├── types.py                # 类型定义（InspectionReport, OptimizationProposal 等）
├── inspector.py            # 代码与结构静态分析
├── proposal_engine.py      # 生成优化提案
├── auto_optimizer.py       # 在约束下尝试低风险变更
└── git_snapshot.py         # 变更前后 Git 快照与回滚
```

Self-Opt 作为编程 API 使用（见下方类型模型）；不再作为 Agent 工具暴露。

## 类型模型

核心类型由 `miniagent.core.self_opt` 包级导出：

```python
from miniagent.core.self_opt import (
    ModuleAnalysis, PainPoint, InspectionReport,
    FileChange, OptimizationProposal, OptimizationResult,
    CodeQualityMetric, OptTestCase, OptTestSummary,
)
```

| 类型 | 说明 |
|------|------|
| `ModuleAnalysis` | 单模块分析结果（文件数、行数、估算覆盖率、质量指标） |
| `PainPoint` | 痛点条目（类型、描述、严重度、影响文件） |
| `InspectionReport` | 完整检查报告（模块分析列表、痛点列表、总体指标） |
| `FileChange` | 文件级变更（操作类型: add/edit/delete、路径、内容） |
| `OptimizationProposal` | 优化提案（标题、描述、信心度、文件变更列表、风险等级） |
| `OptimizationResult` | 优化执行结果（成功/失败、测试摘要） |
| `CodeQualityMetric` | 质量指标（名称、值） |
| `OptTestCase` / `OptTestSummary` | 测试用例定义与执行摘要 |

## 工作流程

```
项目代码 → inspector 静态分析 → InspectionReport
                                      ↓
                        proposal_engine 生成提案 → list[OptimizationProposal]
                                      ↓
                        auto_optimizer 评估 & 应用 → OptimizationResult
                                      ↓
                        git_snapshot 快照 → 可回滚
```

### 1. Inspector（代码分析器）

对项目进行静态扫描，逐模块分析并识别痛点：

- 文件数 / 行数统计
- 估算测试覆盖率
- 模块质量指标（docstring 覆盖率、函数/类密度等）
- 痛点识别（长文件、低覆盖、无文档等）

```python
from miniagent.core.self_opt.inspector import inspect_project

report = await inspect_project(root="/path/to/project")
# report: InspectionReport
#   - modules: list[ModuleAnalysis]
#   - pain_points: list[PainPoint]
#   - metrics: list[CodeQualityMetric]
```

### 2. ProposalEngine（提案引擎）

基于痛点生成可读的优化提案：

- 从 `PainPoint` 转换为 `OptimizationProposal`
- 生成测试相关提案（补充缺失测试）
- 每个提案含信心度与风险等级

```python
from miniagent.core.self_opt.proposal_engine import generate_proposals

proposals = await generate_proposals(report, root="/path/to/project")
# proposals: list[OptimizationProposal]
#   每个提案含 title、description、confidence、changes、risk_level
```

### 3. AutoOptimizer（自动优化器）

在约束下安全地应用优化提案：

- 逐文件变更（创建/编辑/删除）
- 应用前后运行验证测试
- 记录执行结果

```python
from miniagent.core.self_opt.auto_optimizer import apply_proposal

result = await apply_proposal(proposal, root="/path/to/project")
# result: OptimizationResult
#   含 success 状态与测试摘要
```

完整编排（分析 → 提案 → 应用）：

```python
from miniagent.core.self_opt.auto_optimizer import run_auto_optimization

result = await run_auto_optimization(
    root="/path/to/project",
    auto_apply=False,    # True 时自动应用低风险提案
    min_confidence=0.5,  # 最低信心度阈值
)
```

### 4. GitSnapshot（版本快照）

在优化前后创建 Git 快照，支持回滚：

```python
from miniagent.core.self_opt.git_snapshot import (
    is_in_git_repo,
    has_uncommitted_changes,
    create_snapshot,
    rollback_snapshot,
)

# 检查
assert is_in_git_repo()
assert not has_uncommitted_changes()

# 快照
sha = create_snapshot("before-optimization")
# ... 应用优化 ...
# 需要回滚时:
# rollback_snapshot(sha)
```

## 环境变量

| 变量 | 作用 |
|------|------|
| `MINIAGENT_SELF_OPT_TOOLS` | **已失效**；self-opt 不再作为 Agent 工具注册 |

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构中的 self_opt 位置
- [CONTRIBUTING.md](CONTRIBUTING.md) — 代码规范与 docstring 约定
