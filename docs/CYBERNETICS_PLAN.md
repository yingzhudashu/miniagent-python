# 工程控制论融入 Mini Agent 架构计划

> Mini Agent Python | 版本: 2.0.2 | 规划稿（实验性）
>
> **Status: Draft / Exploratory — 待确认是否仍在进行**
>
> 基于钱学森《Engineering Cybernetics》(1954) 中心思想
> 创建日期: 2026-05-09

---

## 📖 《工程控制论》中心思想提炼

1954 年 McGraw-Hill 出版，是控制论在工程领域的奠基之作。

### 核心概念

| 概念 | 含义 | 本质 |
|------|------|------|
| **反馈 (Feedback)** | 系统输出回送到输入端，形成闭环 | 自我感知、自我修正 |
| **稳定性 (Stability)** | 系统在扰动后能否回到平衡态 | 不发散、不失控 |
| **可控性 (Controllability)** | 能否通过控制输入使系统达到任意状态 | 可操作、可达目标 |
| **可观测性 (Observability)** | 能否从输出推断系统内部状态 | 可诊断、可理解 |
| **传递函数 (Transfer Function)** | 输入→输出的数学映射 | 行为预测 |
| **最优控制 (Optimal Control)** | 在约束下寻找最优控制律 | 资源最优利用 |
| **自适应 (Adaptive Control)** | 系统参数变化时自动调整控制策略 | 环境适应 |
| **系统辨识 (System Identification)** | 从观测数据反推系统模型 | 认知世界 |

### 一句话总结

> **用数学方法描述和设计具有反馈的自动控制系统，使系统在不确定性中保持稳定并达到最优。**

---

## 🔗 与 Mini Agent 架构的映射

现有架构已经暗含了控制论的若干概念：

| 控制论概念 | 现有对应 | 成熟度 |
|-----------|---------|--------|
| 反馈 | ReAct 循环 (Think→Act→Observe) | ✅ 已有 |
| 稳定性 | LoopDetector 循环检测 | ✅ 已有（简单） |
| 状态观测 | DefaultToolMonitor 性能统计 | ✅ 已有 |
| 自适应 | self_opt 自我优化子系统 | ✅ 已有（初级） |
| 最优控制 | planner 规划阶段选择工具箱 | ⚠️ 隐式 |
| 系统辨识 | 三层记忆系统 | ⚠️ 未建模 |
| 可控性 | 命令调度 + 会话锁 | ✅ 已有 |
| 传递函数 | — | ❌ 缺失 |

**结论：架构方向正确，但缺乏控制论的"数学化"抽象。**

---

## 📋 融入计划

### Phase 1：形式化反馈控制器（低改动，高收益）

**目标**：把 ReAct 循环从"经验循环"升级为"受控反馈系统"。

**新增模块**：`miniagent/core/feedback_controller.py`

#### 功能设计

```
1. Error Signal (误差信号)
   - 目标 vs 当前输出的偏差度量
   - 每轮 ReAct 计算 "距离目标还有多远"

2. Convergence Monitor (收敛监测)
   - 跟踪误差随轮次的变化趋势
   - 误差递增 → 发散预警 → 触发提前终止
   - 误差稳定下降 → 继续执行
   - 误差停滞 → 触发策略切换

3. Stability Index (稳定性指数)
   - 综合考量: 工具成功率、重复率、误差变化率
   - 输出: stable / oscillating / diverging
```

#### 融入位置

- Executor 的 ReAct 循环中，每轮调用 `feedback_controller.step()`
- 替代/增强现有 LoopDetector（从规则判断升级为指标驱动）

#### 改动量

新增 1 个文件，修改 `executor.py` 约 20 行

---

### Phase 2：状态空间建模（中等改动）

**目标**：让 Agent 对自身执行状态有"可观测性"。

**新增模块**：`miniagent/core/state_observer.py`

#### 功能设计

```
1. 定义 Agent 的状态向量:
   state = [
     context_usage_ratio,      # 上下文窗口使用率
     tool_success_rate,        # 工具成功率
     convergence_velocity,     # 误差变化速度
     memory_recall_score,      # 记忆检索相关度
     token_budget_remaining,   # 剩余 token 预算
   ]

2. 状态估计:
   - 每轮执行后更新状态向量
   - 提供 readable_state() 给 .status 命令

3. 状态迁移记录:
   - 记录 state_t → state_{t+1} 的变迁
   - 用于事后分析和自我优化
```

#### 融入位置

- 与 `context.py` 的 token 追踪结合
- 与 `monitor.py` 的工具统计结合
- 增强 `.status` 命令的输出

#### 改动量

新增 1 个文件，修改 `context.py` + `monitor.py` 各少量

---

### Phase 3：自适应策略引擎（较大改动）

**目标**：从"固定策略"升级为"自适应控制"。

**新增模块**：`miniagent/core/adaptive_policy.py`

#### 功能设计

```
根据当前状态自动调整执行策略:

if state == "stable":
    strategy = "normal"       # 正常执行
elif state == "oscillating":
    strategy = "simplify"     # 简化问题，减少工具调用
elif state == "diverging":
    strategy = "terminate"    # 提前终止，返回最佳部分结果
elif state == "stuck":
    strategy = "replan"       # 重新规划（回到 Phase 1）
elif state == "context_full":
    strategy = "compress"     # 压缩上下文
```

#### 融入位置

- `executor.py` 的 ReAct 循环决策点
- `planner.py` 的规划策略选择
- 与 `self_opt` 子系统联动

#### 改动量

新增 1 个文件，修改 `executor.py` + `planner.py` 约 30-50 行

---

### Phase 4：传递函数近似（研究性质）

**目标**：学习"工具组合 → 输出质量"的映射关系。

#### 概念设计

```
- 将每次完整执行视为一次"系统响应"
- 记录: (输入复杂度, 工具链, 轮次数) → (回复质量评分)
- 长期积累后，Agent 能预判"这个任务大概需要几步、用什么工具"
- 类似控制论中的传递函数 G(s) = Y(s)/U(s)
```

#### 实现思路

- 利用 `activity_log` 中已有的详细记录
- 新增 `quality_scorer`（简单启发式评分）
- 建立 `(task_features) → (expected_cost)` 的查找表

#### 改动量

较小，主要是数据分析层

---

## 📊 总体评估

| 维度 | 评分 | 说明 |
|------|------|------|
| **可行性** | ⭐⭐⭐⭐ | 现有架构已具备基础，不需要大改 |
| **收益** | ⭐⭐⭐⭐ | 能显著提升稳定性和可观测性 |
| **风险** | ⭐⭐ | 增量改动，每步可独立验证 |
| **工作量** | 中等 | Phase 1-2 约 2-3 小时，Phase 3-4 约 4-6 小时 |

### 推荐执行顺序

1. **Phase 1**（反馈控制器）→ 立竿见影，改善 ReAct 循环稳定性
2. **Phase 2**（状态观测）→ 增强 .status 和可观测性
3. **Phase 3**（自适应策略）→ 让 Agent 能"见机行事"
4. **Phase 4**（传递函数）→ 长期收益，积累数据后才有价值
