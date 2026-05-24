# 控制论子系统（Cybernetics）

> Mini Agent Python | 控制论闭环：StateObserver → FeedbackController → AdaptivePolicy

## 概述

控制论子系统将**反馈控制理论**引入 Agent 的 ReAct 执行循环，通过持续观测执行状态、计算综合误差、判定收敛趋势并施加自适应策略，使 Agent 能够在遇到困境时自动调整行为（终止、简化、压缩上下文等），避免无效循环与资源浪费。

## 架构

```
ReAct 执行循环
    │
    ├─ 工具执行后触发 ─┐
    │                 ↓
    │          ┌──────────────────┐
    │          │ StateObserver    │  ← 观测：上下文使用率、工具成功率、
    │          │                  │     唯一调用比、Token 预算
    │          └────────┬─────────┘
    │                   ↓
    │          ┌──────────────────┐
    │          │ FeedbackController│ ← 反馈：计算综合误差、斜率、
    │          │                  │     收敛速度、稳定性指数
    │          └────────┬─────────┘
    │                   ↓
    │          ┌──────────────────┐
    │          │ AdaptivePolicy   │ ← 决策：TERMINATE / CONVERGED /
    │          │                  │     SIMPLIFY / COMPRESS / CONTINUE
    │          └────────┬─────────┘
    │                   ↓
    └─ 策略生效 ←──────┘
```

**启用方式**：环境变量 `MINIAGENT_CONTROL_THEORY`（默认开启，设为 `0`/`false`/`off` 可关闭）。

## StateObserver — 状态观测器

**位置**：`miniagent/core/state_observer.py`

观测器在每轮 ReAct 后收集以下指标，构建 `AgentState` 快照：

| 指标 | 字段 | 说明 |
|------|------|------|
| 上下文使用率 | `context_usage_ratio` | 已用 Token / 上下文窗口 |
| 工具成功率 | `tool_success_rate` | 成功调用次数 / 总调用次数 |
| 收敛速度 | `convergence_velocity` | 来自 FeedbackController 的最新误差值（负表示递减） |
| 唯一工具调用比 | `unique_tool_call_ratio` | 不同工具名数 / 总调用次数（检测工具重复） |
| Token 预算剩余 | `token_budget_remaining` | 剩余可用 Token 数 |
| 总工具调用数 | `total_tool_calls` | 累积调用次数 |
| 当前轮次 | `current_turn` | ReAct 轮次计数 |

**观测时机**：在 `execute_plan()` 的工具执行阶段结束后调用 `observe()`。

## FeedbackController — 反馈控制器

**位置**：`miniagent/core/feedback_controller.py`

### 综合误差公式

```
error = w1 * error_estimate + w2 * tool_failure_rate + w3 * tool_repeat_rate
```

| 权重 | 常量 | 值 | 理由 |
|------|------|-----|------|
| 当前误差 | `_WEIGHT_CURRENT` | 0.5 | 对即时误差最敏感 |
| 变化率 | `_WEIGHT_VELOCITY` | 0.3 | 兼顾趋势方向 |
| 加速度 | `_WEIGHT_ACCELERATION` | 0.2 | 捕捉震荡模式 |

### 斜率计算

使用最小二乘法拟合最近 `window_size`（默认 5）轮的综合误差序列，计算线性回归斜率：

- **斜率 < 0**：误差递减 → 收敛中
- **斜率 > 0**：误差递增 → 发散中
- **斜率 ≈ 0**：稳定（可能震荡或停滞）

### 收敛速度与稳定性

| 指标 | 计算 | 含义 |
|------|------|------|
| 收敛速度 | `slope` | 负值越大收敛越快 |
| 稳定性指数 | `1 - |slope| / (|mean_error| + ε)` | 接近 1 表示稳定 |

### 状态机

控制器维护内部状态（`_state`）：

| 状态 | 条件 | 含义 |
|------|------|------|
| `STABLE` | 误差波动小，斜率接近 0 | 正常执行 |
| `CONVERGING` | 斜率 < 0 且误差下降 | 正向收敛 |
| `DIVERGING` | 斜率 > 0 且误差上升 | 需要干预 |
| `OSCILLATING` | 误差交替升降 | 工具调用可能陷入循环 |

## AdaptivePolicy — 自适应策略

**位置**：`miniagent/core/adaptive_policy.py`

### 决策表

| 决策 | 触发条件 | 效果 |
|------|----------|------|
| `TERMINATE` | 误差超过阈值且连续 N 轮发散 | 提前终止 ReAct 循环，返回当前最佳回复 |
| `CONVERGED` | 误差低于收敛阈值且稳定 | 提前终止，认定已达成目标 |
| `SIMPLIFY` | 工具重复率过高 + 误差上升 | 移除低效工具，仅保留基础工具 |
| `COMPRESS` | 上下文使用率超过阈值 | 触发上下文压缩（保留关键消息） |
| `CONTINUE` | 未达到上述条件 | 正常继续 ReAct 循环 |

### 防抖动机制

`AdaptivePolicy` 维护内部计数器防止频繁决策：

- `_stuck_turns`：连续停滞轮次计数
- `_diverge_turns`：连续发散轮次计数
- `reset()`：每次会话开始前调用以清除历史状态

## 与 ReAct 循环的集成

在 `miniagent/core/executor.py` 的 `execute_plan()` 中，控制论闭环在每轮工具执行后触发：

```python
# 伪代码
for turn in range(max_turns):
    msg = await llm_call()
    tool_results = await execute_tools(msg)

    # 控制论闭环
    state = observer.observe(context_manager, monitor, controller)
    report = controller.analyze(state)
    decision = policy.decide(state, report)

    if decision.action == "TERMINATE":
        break  # 提前终止
    elif decision.action == "SIMPLIFY":
        simplify_tools()  # 精简工具箱
    elif decision.action == "COMPRESS":
        compress_context()  # 压缩上下文
```

**可见性**：所有控制论决策通过 `on_thinking` 回调以 `[自适应调整]` header 推送至 CLI 和飞书通道（`streaming=True`）。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `MINIAGENT_CONTROL_THEORY` | `1` | 是否启用控制论闭环（`0`/`false`/`off` 关闭） |

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构，Phase 2 执行阶段
- [PERFORMANCE.md](PERFORMANCE.md) — 性能场景中的控制论效果
