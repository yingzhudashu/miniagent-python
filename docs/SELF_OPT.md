# Self-Opt 自优化文档

> Mini Agent Python — Self-Optimization 子系统

## 概述

Self-Opt 是 Mini Agent 的自优化机制，通过分析和优化提示词来提升 Agent 表现。

## 模块结构

```
miniagent/core/self_opt/
├── __init__.py          # 包入口
├── types.py             # 类型定义（SelfOptConfig, AnalysisResult, Proposal）
├── inspector.py         # 对话分析器（分析 LLM 调用日志）
├── auto_optimizer.py    # 自动优化器（生成优化方案）
├── proposal_engine.py   # 提案引擎（评估和应用优化）
└── git_snapshot.py      # Git 快照（优化前后的版本对比）
```

## 工作流程

```
对话日志 → Inspector 分析 → Optimizer 生成方案 → Proposal Engine 评估 → 应用优化
```

### 1. Inspector（分析器）

分析历史对话，识别问题模式：
- 工具调用失败率
- 重复执行相同操作
- 回复质量下降
- 上下文丢失

```python
from miniagent.core.self_opt import Inspector

inspector = Inspector()
result = await ins inspector.analyze(conversation_history)
```

### 2. AutoOptimizer（自动优化器）

根据分析结果生成优化提案：
- 提示词调整
- 工具选择优化
- 上下文管理改进

```python
from miniagent.core.self_opt import AutoOptimizer

optimizer = AutoOptimizer()
proposal = await optimizer.generate(analysis_result)
```

### 3. ProposalEngine（提案引擎）

评估优化方案的可行性，应用最优方案：
- 评分排序
- 冲突检测
- 渐进式应用

```python
from miniagent.core.self_opt import ProposalEngine

engine = ProposalEngine()
await engine.apply(proposal)
```

### 4. GitSnapshot（版本快照）

在应用优化前后创建 Git 快照，便于回滚：
- 优化前快照
- 优化后快照
- 差异对比

```python
from miniagent.core.self_opt import GitSnapshot

snapshot = GitSnapshot()
await snapshot.capture("before_optimization")
# ... 应用优化 ...
await snapshot.capture("after_optimization")
```

## 配置

```python
from miniagent.core.self_opt import SelfOptConfig

config = SelfOptConfig(
    analysis_window=50,      # 分析窗口大小（最近 N 条对话）
    min_confidence=0.7,      # 最低置信度
    auto_apply=False,        # 是否自动应用（推荐手动确认）
)
```

## 类型定义

### AnalysisResult

```python
@dataclass
class AnalysisResult:
    issues: list[Issue]       # 发现的问题
    metrics: dict[str, float] # 量化指标
    timestamp: float          # 分析时间
```

### Proposal

```python
@dataclass
class Proposal:
    title: str           # 提案标题
    description: str     # 详细描述
    confidence: float    # 置信度 (0-1)
    changes: list[Change] # 具体变更
    risk_level: str      # 风险等级：low/medium/high
```

## 架构说明

Self-Opt 模块已补全，包含 5 个文件。
类型定义在 `miniagent/core/self_opt/types.py`。
