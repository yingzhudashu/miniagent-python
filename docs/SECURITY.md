# 安全模型

> Mini Agent Python | 版本: 3.0.0 | 最后更新: 2026-07-15 | 与 `miniagent.__version__` 对齐 | 模块: `miniagent/assistant/security/` + 全局安全策略

## 安全架构概览

```
┌──────────────────────────────────────────────────┐
│                    用户输入                        │
│              (CLI / 飞书消息)                      │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│              消息队列 (MessageQueue)               │
│         按 chat_id 隔离，防止跨会话干扰             │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│               沙箱环境 (Sandbox)                   │
│    路径白名单 │ 父目录遍历拦截 │ 权限策略           │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│              工具执行 (Tool Layer)                  │
│    文件操作 │ 命令执行 │ 网页访问                   │
└──────────────────────────────────────────────────┘
```

## 1. 沙箱机制

**位置**: `miniagent/assistant/security/sandbox.py`

### 路径白名单

所有文件操作（读、写、删除）必须在白名单目录内执行：

```python
# 默认工作空间
workspace = get_default_workspace()  # {paths.state_dir}/sessions/<session_id>/files/（见 [ENGINEERING.md](ENGINEERING.md) §3）

# 路径验证
resolved = resolve_sandbox_path(path, ["/app/workspace"])
# 如果路径不在 allowed_dirs 中 → 抛出 SandboxViolationError
```

### 父目录遍历拦截

```python
# ❌ 被拦截（相对路径基于进程 cwd 解析）
resolve_sandbox_path("../../etc/passwd", ["/app/workspace"])
# → SandboxViolationError

# ✅ 允许（绝对路径，或工具层已相对 ctx.cwd 拼接后的路径）
resolve_sandbox_path("/app/workspace/data/output.txt", ["/app/workspace"])
# → /app/workspace/data/output.txt

# 文件工具应使用 path_utils，相对路径相对会话 cwd 而非进程 cwd：
# resolve_path_from_ctx("data/output.txt", ctx)  # ctx.cwd="/app/workspace"
```

### 权限模型（两层，勿混淆）

| 层级 | 字段 | 含义 |
|------|------|------|
| 工具元数据 | ``ToolDefinition.permission`` | ``sandbox`` / ``allowlist`` / ``require-confirm`` |
| 运行时上下文 | ``ToolContext.permission`` | ``sandbox`` / ``allowlist`` / ``full``（仅调试） |

- **路径沙箱**：文件类工具通过 ``resolve_path_for_tool`` 校验 ``allowed_paths``，与 ``ctx.permission`` 无关。
- **命令安全**：``exec_command`` 在 ``ctx.permission != "full"`` 时**始终**启用黑名单、注入检测与命令白名单（生产默认 ``allowlist`` 也会检查）。
- **用户确认**：``require-confirm`` 工具（如 ``delete_file``、``install_skill``）由 ``executor.execute_plan`` 经 ``ConfirmationChannel`` 拦截；``AgentConfig.auto_execute_confirmed=True`` 可跳过。

## 2. 命令执行安全

**位置**: `miniagent/assistant/tools/exec.py`

### subprocess 调用约束

- 使用 `asyncio.create_subprocess_shell()` 执行命令
- 设置超时限制，防止无限运行
- 输出截断，防止内存溢出
- 工作目录须在 ``allowed_paths`` 沙箱内（``_validate_exec_cwd``）
- 除 ``ctx.permission="full"``（调试）外，始终启用危险命令黑名单、Shell 注入检测与命令白名单

### 危险命令防护

生产 executor 注入 ``ToolContext(permission="allowlist")``；**仍会**执行下列检查（见 ``exec._command_security_enabled``）：

```python
# 三层检查（permission != "full" 时）
# 1. 危险命令黑名单（rm -rf /、mkfs 等）
# 2. Shell 注入模式检测
# 3. 命令 basename 白名单（可配置 security.allowed_commands）
```

Windows 下命令名按系统语义进行大小写无关匹配，并将 `.exe`、`.com`、`.bat`、`.cmd`
视为同一 basename 的标准可执行别名；例如白名单中的 `curl` 可匹配 `curl.exe`。
其他扩展名仍须显式加入白名单。

## 3. 多实例安全

**位置**: `miniagent/assistant/engine/session_lock.py` + `miniagent/assistant/infrastructure/instance.py`

### 会话锁

- 每个会话使用 `.lock` 文件互斥
- 锁文件记录 PID，支持过期锁检测（进程退出后自动清理）
- 防止多个实例同时修改同一会话（**尽力互斥**，非 flock 级严格锁；极端并发下可能短暂双占）
- 同步 API（`try_lock_session`）在 Windows 上会阻塞线程；asyncio 上下文请用 `try_lock_session_async`
- `is_session_locked` 对陈旧锁返回 `None`（视为未占用），实际加锁时由 `try_lock_session*` 清理文件

```python
# 加锁
ok, reason = try_lock_session("default")

# 检查
lock_pid = is_session_locked("default")

# 释放
release_session_lock("default")
```

### 实例注册表

每个实例在 `workspaces/instances/<id>/` 注册；**存活判定**以 OS PID 为准（详见 [ENGINEERING.md §3.3](ENGINEERING.md#33-多实例注册表)）。

## 4. 循环检测

**位置**: `miniagent/agent/loop_detector.py`

防止 Agent 陷入无限循环：

| 级别 | 触发条件 | 处理方式 |
|------|---------|---------|
| `warning` | 相同工具+参数重复 8 次 | 日志警告 |
| `critical` | 相同工具+参数重复 12 次 | 终止执行 |

## 5. 飞书凭证安全

### 凭证存储

- **App ID / App Secret** 存储在 `config.user.json` 的 `secrets` 部分
- `config.user.json` 已加入 `.gitignore`，不会提交到版本控制
- 运行时自动加载到环境变量

### 消息安全

- 内存 + 磁盘双重去重，防止重复处理
- 消息防抖合并（``feishu.message_debounce_ms``，见 ``message_debounce.py``），防止同一发送者短时连发被拆成多轮 Agent
- WebSocket 长连接模式，无需暴露公网端口

## 6. 数据安全原则

| 原则 | 实现 |
|------|------|
| 最小权限 | 工具只能访问白名单路径 |
| 会话隔离 | 每个 chat_id 独立队列和工作空间 |
| 输入验证 | 路径解析前验证，防止注入 |
| 输出截断 | LLM 响应和工具输出限制长度 |
| 密钥不落库 | 仓库内不出现真实 token；`config.user.json` 不入版本控制 |
| 错误隔离 | 单个工具异常不影响 Agent 主流程 |

### 外部排障检索

`builtin-stackexchange` 会把排障关键词发送到 Stack Exchange 公开 API。工具在发送前会替换常见
凭据、邮箱、私有 URL/主机、内网 IP 和本地绝对路径，并在结果元数据中标记是否发生脱敏；这只是
纵深防御，用户和 Agent 仍不得把内部源码、客户数据或完整私有日志作为搜索词。返回的社区答案
不会自动执行，采纳状态和票数仅作为经验信号；涉及提权、固件、注册表、分区或网络修改时必须先
解释风险并结合本地证据验证。

说明：密钥存在于**进程环境**（由 `config.user.json` 的 `secrets` 部分自动注入）属预期行为；「不写入日志」依赖默认日志字段与关闭过度调试。

## 7. 安全配置检查清单

- [ ] `config.user.json` 文件权限设置为 600（仅所有者可读写）
- [ ] `config.user.json` 已加入 `.gitignore`
- [ ] `AGENT_DEBUG=false`（生产环境）
- [ ] 飞书应用已设置 IP 白名单（如适用）
- [ ] 工作空间目录权限正确；共享机上前缀 `MINIAGENT_PATHS_STATE_DIR` 到用户私有目录
- [ ] 定期检查 `workspaces/instances/` 无残留死实例

## 8. 相关文档

- [ENGINEERING.md](ENGINEERING.md)：`config.user.json` 与密钥不入库、CI 质量门禁、多实例与磁盘注册目录语义（§3.3）。
