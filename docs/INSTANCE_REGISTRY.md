# 实例注册表（Instance Registry）

> Mini Agent Python | 版本: 2.1.0

## 概述

MiniAgent 使用 **RuntimeContext 组合根** 模式管理进程级实例，而非传统的全局变量散布。实例注册表是 RuntimeContext 的核心组件之一，负责：

- **多实例管理**：支持多进程部署，每个实例有独立 ID 和心跳
- **PID 存活检测**：权威判定实例存活状态
- **项目目录冲突检测**：防止同一项目目录被多实例抢占
- **跨进程文件锁保护**：确保注册表操作安全

## 当前架构

### RuntimeContext 组合根

所有进程级依赖通过 `RuntimeContext` 聚合，在 `miniagent/compat.py` 的 `unified_entry()` 中构造：

```python
# miniagent/runtime/context.py
class RuntimeContext:
    registry: ToolRegistryProtocol      # 工具注册表
    monitor: ToolMonitorProtocol        # 工具监控器
    engine: UnifiedEngine | None        # 主引擎（可选）
    message_queue: MessageQueueManager  # 消息队列
    channel_router: ChannelRouter       # 通道路由
    feishu: FeishuRuntime | None        # 飞书运行时（可选）
    memory_store: MemoryStoreProtocol   # 记忆存储
    activity_log: ActivityLogProtocol   # 活动日志
    keyword_index: KeywordIndexProtocol # 关键词索引
    ...
```

### InstanceRegistry 实现

位置：`miniagent/infrastructure/instance.py`

**核心功能**：
- `register_instance()`：注册新实例，返回自增 ID
- `heartbeat()`：更新实例心跳时间戳
- `stop_instance()`：注销实例，清理目录
- `list_instances()`：列出所有实例（Markdown/表格格式）
- `check_alive()`：PID 存活检测（跨平台）

**设计特点**：
- 线程安全（`threading.Lock`）
- 跨进程文件锁（`fcntl.flock` / Windows 等效）
- PID 存活检测作为权威判定
- 僵尸目录清理（非心跳超时）

## 使用方式

### 正常使用

实例注册通过 Engine 初始化自动完成：

```python
# miniagent/engine/main.py
def main():
    ctx = RuntimeContext(...)
    ctx.instance_id = register_instance(
        ctx.state_dir,
        project_dir=ctx.project_dir,
    )
    ...
```

### CLI 查看

```bash
miniagent /instance
```

输出示例：
```
🏭 实例: #42
📁 项目: D:\AIhub\miniagent-python
💓 心跳: 2026-06-07 14:30:00
🟢 状态: alive
```

### 测试场景

使用 `conftest.py` 提供的 fixture 重置进程单例：

```python
# tests/conftest.py
@pytest.fixture(autouse=True)
def _reset_process_singletons_after_test():
    yield
    reset_instance_registry_for_tests()
```

## 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `paths.state_dir` | 状态目录（实例注册表存储位置） | `workspaces` |
| `agent.parallel_sessions` | 并行会话上限 | 10 |

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构设计（§4 RuntimeContext）
- [ENGINEERING.md](ENGINEERING.md) - 多实例与质量门禁
- [CHANNEL_BINDING.md](CHANNEL_BINDING.md) - 通道绑定

## 变更历史

- 2026-06-05: 创建文档（Phase 2 任务）
- 2026-06-07: 更新为 RuntimeContext 组合根架构（Phase 3 完成）