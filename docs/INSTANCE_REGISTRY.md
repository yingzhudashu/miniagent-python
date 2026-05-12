# 多实例注册表

> 模块: `miniagent/infrastructure/instance.py` | 版本: 2.0.2

本文说明 **多进程并行运行** 时的磁盘注册布局、存活判定与生命周期。**不会**替代源码阅读：行为以仓库内实现为准。

## 目的

- 允许在同一台机器上打开多个终端，各自运行 `python -m miniagent`，互不抢占全局 PID 锁。
- 为 `.instance list`、`python -m miniagent --stop` 等能力提供「当前有哪些 Agent 进程」的数据源。
- 进程异常退出时，尽量自动回收磁盘上的僵尸注册目录（见下文）。

## 状态目录与环境变量

- 默认状态根目录：`<当前工作目录>/workspaces`。
- 可通过环境变量 **`MINI_AGENT_STATE`** 指向任意目录（测试与 CI 强烈建议使用临时路径，避免污染本机 `workspaces/`）。
- 实例注册路径：**`<状态根>/instances/<数字ID>/`**。

每个实例目录通常包含：

| 文件 | 说明 |
|------|------|
| `meta.json` | `pid`、`instance_id`、`mode`、`start_time`、`active_sessions`、`hostname` 等 |
| `heartbeat` | 可选时间戳文本；主循环会周期性刷新；**存活判定以操作系统 PID 为准**，心跳仅作观测 |

## 生命周期

1. **注册**：CLI 主流程启动时调用 `register_instance()` → `InstanceRegistry.register()`。
2. **启动前清理**：在分配新 `instance_id` 之前，会扫描已有数字子目录；若 `meta.json` 中的 **PID 已不存在**（或无效），则 **仅删除该注册目录**，不会对其它 PID 发送终止信号。
3. **运行中**：进程按既定间隔调用 `heartbeat()` 更新心跳文件（便于人工排查；不参与「是否存活」的权威判定）。
4. **正常退出**：应调用 `unregister_instance()`，删除本实例目录。
5. **列出实例**：`list_all()` / `list_instances()` 遍历目录；同样以 **PID 是否存在** 判定存活；未运行的目录会被删除后不出现在列表中。

## 存活判定（与「不误杀运行中进程」）

- **存活**：`meta.json` 中 `pid` 为正整数，且 `_is_process_running(pid)` 为真（Windows：`tasklist`；POSIX：`os.kill(pid, 0)`）。
- **未运行**：PID 缺失、非正整数，或进程已退出 → 视为僵尸注册，仅清理磁盘目录。
- **注意**：清理逻辑 **不会** `taskkill`/`kill` 其它实例；只有用户执行 `--stop` 或 `.instance stop` 等「停止」路径时才会主动终止目标 PID。
- **PID 复用**：极端情况下旧 PID 被新无关进程占用，可能被误判为「仍存活」而暂时保留目录——属于「宁可少删、避免误删」的权衡。

## 与 `list_all()` 的一致性

`register()` 启动前清理与 `list_all()` 使用相同的 **`_is_pid_alive`** 语义，避免「刚启动扫了一遍、列表又扫出另一套规则」的分叉。

## 定时任务调度锁（`scheduler.lock`）

- 路径：**`<状态根>/scheduled_tasks/scheduler.lock`**（见 `miniagent/scheduled_tasks/lock.py`）。
- 进程被 **`os._exit`、硬杀或崩溃** 时，锁文件可能短时间残留；**下一进程**在 `try_acquire_scheduler_lock` 中若发现锁内 PID 已不存在，会删除锁并重试，语义与上文实例目录的 **PID 存活判定** 类似。

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 多实例与并发安全总览
- [DEPLOYMENT.md](DEPLOYMENT.md) — 部署与环境变量
- [CLI.md](CLI.md) — `.instance` 命令
