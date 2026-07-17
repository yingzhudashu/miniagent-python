# Self-Opt 自优化文档

> Mini Agent Python | 版本: 4.0.0 | 最后更新: 2026-07-17 | 与 `miniagent.__version__` 对齐 | Self-Optimization Agent 扩展

## 概述

Self-Opt 是 Mini Agent 的自我优化机制，包含两个核心能力：

1. **代码静态分析**：通过对项目代码进行静态分析和结构检查，识别质量问题和痛点，生成优化提案
2. **运行日志驱动**：从 Trace 系统和活动日志中提取运行指标，识别性能瓶颈、高频错误、异常行为，生成优化提案

> **双重优化源**：代码分析关注代码质量和结构；运行日志关注性能和稳定性。二者互补，可合并排序。

## 配置

`self_optimization` 节在 `miniagent/assistant/resources/config.defaults.json` 中配置；运行分析依赖 Trace 数据，Trace 配置见 **[ENGINEERING.md §5](ENGINEERING.md#5-trace-系统全链路监控)**（SSOT）。

```json
{
  "self_optimization": {
    "enabled": true,
    "auto_apply": false,
    "auto_apply_max_risk": "low",
    "proposal_output_dir": "workspaces/self_opt/proposals",
    "runtime_analysis_enabled": true,
    "code_analysis_enabled": true,
    "proposal_retention_days": 30,
    "min_failure_rate_threshold": 0.05,
    "min_duration_ms_threshold": 2000
  }
}
```

**关键配置**：
- `auto_apply: false`：默认仅生成提案，需人工批准执行；设为 `true` 可自动执行低风险提案
- `auto_apply_max_risk: "low"`：自动执行时仅允许低风险提案
- `runtime_analysis_enabled: true`：启用运行日志分析（需 `trace.enabled: true`，见 [ENGINEERING.md §5](ENGINEERING.md#5-trace-系统全链路监控)）

## CLI 命令

### 查看状态

```bash
/self-opt status
```

显示：
- 系统启用状态
- auto_apply 配置
- 提案存储路径
- 今日提案数量

### 列出提案

```bash
/self-opt proposals [status]
```

按状态过滤（pending/approved/rejected/executing/completed/failed）。

### 查看提案详情

```bash
/self-opt show <proposal_id>
```

显示完整提案信息：类型、风险等级、目标、描述、理由、预期收益、文件变更、测试用例。

### 批准提案

```bash
/self-opt approve <proposal_id>
```

将 pending 提案标记为 approved，允许后续执行。

### 拒绝提案

```bash
/self-opt reject <proposal_id>
```

将 pending 提案标记为 rejected，不再执行。

### 执行提案

```bash
/self-opt apply <proposal_id> [root]
```

执行已批准或待执行的提案（**不依赖** `auto_apply` 配置）：
- 低风险/中风险：`pending` 或 `approved` 状态均可执行
- 高风险：须先 `/self-opt approve` 变为 `approved` 后再执行
- 无可执行内容（无 files 且无 test_cases）的提案会跳过
- 应用文件变更、运行验证测试、失败时自动回滚（Git stash 或文件级备份）

### 触发分析

```bash
/self-opt analyze
```

手动触发分析（受 `runtime_analysis_enabled` / `code_analysis_enabled` 控制）：
- 运行日志：Trace 事件、循环检测、上下文压缩、LLM/工具/错误统计
- 代码静态：项目结构扫描（`code_analysis_enabled: true` 时）
- 合并去重后生成优化提案并保存；更新 `history.json` 索引

### 查看报告

```bash
/self-opt report [date]
```

显示指定日期的运行分析报告：
- 摘要统计
- 工具性能指标
- LLM 调用统计
- 错误汇总
- 问题标记

## 模块结构

```
miniagent/assistant/self_opt/
├── __init__.py             # 包入口，导出类型模型
├── types.py                # 类型定义（InspectionReport, OptimizationProposal 等）
├── inspector.py            # 代码与结构静态分析
├── proposal_engine.py      # 生成优化提案（代码分析）
├── proposal_generator.py   # 生成优化提案（运行日志）
├── proposal_store.py       # 提案持久化存储与状态管理
├── runtime_analyzer.py     # 运行日志分析（从 trace/activity_log 提取指标）
├── auto_optimizer.py       # 在约束下尝试低风险变更
└── git_snapshot.py         # 变更前后 Git 快照与回滚
```

## 类型模型

核心类型由 `miniagent.assistant.self_opt` 包级导出：

```python
from miniagent.assistant.self_opt import (
    ModuleAnalysis, PainPoint, InspectionReport,
    FileChange, OptimizationProposal, OptimizationResult,
    CodeQualityMetric, OptTestCase, OptTestSummary,
    ProposalStore, RuntimeAnalyzer, ProposalGenerator,
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
| `ProposalStore` | 提案持久化存储与状态管理 |
| `RuntimeAnalyzer` | 运行日志分析器 |
| `ProposalGenerator` | 运行日志驱动提案生成器 |

## 工作流程

### 代码静态分析流程

```
项目代码 → inspector 静态分析 → InspectionReport
                                      ↓
                        proposal_engine 生成提案 → list[OptimizationProposal]
                                      ↓
                        ProposalStore 保存提案 → proposals-{YYYY-MM-DD}.jsonl
                                      ↓
                        人工批准 → apply_proposal 执行
                                      ↓
                        git_snapshot 快照 → 可回滚
```

### 运行日志驱动流程

```
Trace 事件 → trace_stats 统计分析 → 运行指标
                                      ↓
                        RuntimeAnalyzer 识别问题 → 分析报告
                                      ↓
                        ProposalGenerator 生成提案 → list[OptimizationProposal]
                                      ↓
                        ProposalStore 保存提案 → proposals-{YYYY-MM-DD}.jsonl
                                      ↓
                        人工批准 → apply_proposal 执行
                                      ↓
                        git_snapshot 快照 → 可回滚
```

### Trace 系统

运行分析依赖 Trace 数据：`trace.enabled: true` 时事件持久化到 `{trace.output_dir}/trace-YYYY-MM-DD-pid{pid}.jsonl`（默认 `workspaces/logs/`，相对进程 cwd，**与** `{paths.state_dir}` **分离**）。事件 schema、writer 与日报 API 见 **[ENGINEERING.md §5](ENGINEERING.md#5-trace-系统全链路监控)**。

## 运行日志分析维度

### 1. 工具调用统计

- 成功率统计
- 平均时延分布
- 慢工具识别（超过 `min_duration_ms_threshold`）
- 高失败率工具（超过 `min_failure_rate_threshold`）

### 2. LLM 调用统计

- 请求次数
- Token 消耗（prompt + completion）
- 平均消息数/工具数

### 3. 错误汇总

- 按类型分组（TimeoutError、PermissionError 等）
- 按工具分组
- 用户误用 vs 工具缺陷标记

### 4. 循环检测

- 重复调用模式
- Ping-pong 行为识别

## 提案生成逻辑

### 慢工具提案

```python
# 条件：avg_ms > min_duration_ms_threshold
proposal = OptimizationProposal(
    type="optimize",
    risk_level="medium",
    target=f"工具: {tool_name}",
    description=f"工具 {tool_name} 平均执行时延 {avg_ms}ms",
    rationale="时延过高影响用户体验",
    expected_benefit="降低平均执行时延",
    estimated_effort=30,
)
```

### 工具失败提案

```python
# 条件：success_rate < 1 - min_failure_rate_threshold
proposal = OptimizationProposal(
    type="refactor",
    risk_level="high",
    target=f"工具: {tool_name}",
    description=f"工具 {tool_name} 成功率仅 {success_rate:.1%}",
    rationale="高失败率可能源于参数校验不完善、错误处理缺失",
    expected_benefit="提升工具稳定性",
    estimated_effort=60,
)
```

### 错误处理提案

```python
# 条件：error_count >= 3
proposal = OptimizationProposal(
    type="optimize",
    risk_level="low" if is_user_error else "medium",
    target=f"错误处理: {error_type}",
    description=f"错误类型 {error_type} 出现 {count} 次",
    rationale="用户误用需改进错误提示；工具缺陷需修复",
    expected_benefit="减少错误发生频率",
    estimated_effort=15,
)
```

### Token 优化提案

```python
# 条件：total_tokens > 100000
proposal = OptimizationProposal(
    type="optimize",
    risk_level="low",
    target="LLM token 消耗",
    description=f"LLM token 消耗过大：prompt {prompt_tokens}, completion {completion_tokens}",
    rationale="高 token 消耗增加 API 成本",
    expected_benefit="降低 API 成本，减少上下文压力",
    estimated_effort=20,
)
```

## 提案持久化

### 文件结构

```
workspaces/self_opt/proposals/   # 默认相对 miniagent 包根/cwd，非 {paths.state_dir}
├── proposals-{YYYY-MM-DD}.jsonl    # 每日提案追加写入
├── history.json            # 提案索引（id、状态、来源、时间戳）
└── reports/
    ├── runtime-{YYYY-MM-DD}.json   # 运行分析报告
    └── trace-report-{YYYY-MM-DD}.json # Trace 统计报告
```

### 提案状态流转

```
pending → approved → executing → completed/failed
pending → rejected
```

### 提案记录格式

```json
{
  "id": "opt-abc123",
  "status": "pending",
  "source": "runtime_analysis",
  "created_at": "2026-06-05T10:00:00Z",
  "updated_at": "2026-06-05T10:00:00Z",
  "proposal": {
    "id": "opt-abc123",
    "type": "optimize",
    "risk_level": "low",
    "target": "工具: read_file",
    "description": "优化 read_file 性能",
    "rationale": "平均时延过高",
    "expected_benefit": "降低平均执行时延",
    "estimated_effort": 30,
    "files": [],
    "test_cases": []
  }
}
```

## API 使用示例

### 触发运行分析

```python
from miniagent.assistant.self_opt import ProposalGenerator

generator = ProposalGenerator()
saved_ids = generator.generate_and_save(date="2026-06-05")

print(f"生成 {len(saved_ids)} 个优化提案:")
for pid in saved_ids:
    print(f"  - {pid}")
```

### 加载提案

```python
from miniagent.assistant.self_opt import ProposalStore

store = ProposalStore()

# 加载所有 pending 提案
proposals = store.load_proposals(status="pending")

# 加载指定提案
record = store.get_proposal("opt-abc123")
```

### 执行提案

```python
from miniagent.assistant.self_opt import ProposalStore

store = ProposalStore()

# 异步执行提案
result = await store.apply_proposal_async(
    "opt-abc123",
    root="/path/to/project",
    auto_rollback=True,
)

print(f"执行结果: {result.status}")
if result.status == "success":
    print(f"  应用变更: {result.changes_applied} 个")
else:
    print(f"  错误: {result.error}")
```

### 生成运行报告

```python
from miniagent.assistant.self_opt import RuntimeAnalyzer

analyzer = RuntimeAnalyzer()
report = analyzer.analyze(date="2026-06-05")
analyzer.save_report(report)

print(f"摘要: {report['summary']}")
print(f"慢工具: {report['tools']['slow_tools']}")
print(f"错误: {report['errors']}")
```

## 相关文档

- [ENGINEERING.md](ENGINEERING.md) — Trace 系统详解
- [CLI.md](CLI.md) — CLI 命令手册（自我优化章节）
- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构中的 self_opt 位置
- [CONTRIBUTING.md](CONTRIBUTING.md) — 代码规范与 docstring 约定
