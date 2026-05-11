# 安全模型

> 模块: `miniagent/security/` + 全局安全策略 | 版本: 2.0.2

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

**位置**: `miniagent/security/sandbox.py`

### 路径白名单

所有文件操作（读、写、删除）必须在白名单目录内执行：

```python
# 默认工作空间
workspace = get_default_workspace()  # workspaces/sessions/<session_id>/files/

# 路径验证
resolved = resolve_sandbox_path(path, cwd, allowed_dirs)
# 如果路径不在 allowed_dirs 中 → 抛出 PermissionError
```

### 父目录遍历拦截

```python
# ❌ 被拦截
resolve_sandbox_path("../../etc/passwd", cwd, ["/app/workspace"])
# → PermissionError: 路径越界

# ✅ 允许
resolve_sandbox_path("data/output.txt", cwd, ["/app/workspace"])
# → /app/workspace/data/output.txt
```

### 权限策略

| 策略 | 说明 |
|------|------|
| `allowlist` | 只允许白名单内的路径（默认） |
| `full` | 允许所有路径（仅调试用） |

## 2. 命令执行安全

**位置**: `miniagent/tools/exec.py`

### subprocess 调用约束

- 使用 `asyncio.create_subprocess_shell()` 执行命令
- 设置超时限制，防止无限运行
- 输出截断，防止内存溢出
- 工作目录限制在沙箱内

### 危险命令防护

```python
# 工具执行上下文
ctx = ToolContext(
    cwd=workspace,           # 工作目录限制
    allowed_paths=[workspace], # 路径白名单
    permission="allowlist",   # 权限策略
)
```

## 3. 多实例安全

**位置**: `miniagent/engine/session_lock.py` + `miniagent/infrastructure/instance.py`

### 会话锁

- 每个会话使用 `.lock` 文件互斥
- 锁文件记录 PID，支持死锁检测
- 防止多个实例同时修改同一会话

```python
# 加锁
ok, reason = try_lock_session("default")

# 检查
lock_pid = is_session_locked("default")

# 释放
release_session_lock("default")
```

### 实例注册表

- 每个实例在 `workspaces/instances/<id>/` 注册
- 心跳机制（30 秒超时），自动清理死实例
- PID 存活检测（Windows: tasklist, Unix: os.kill）

## 4. 循环检测

**位置**: `miniagent/infrastructure/loop_detector.py`

防止 Agent 陷入无限循环：

| 级别 | 触发条件 | 处理方式 |
|------|---------|---------|
| `warning` | 相同工具+参数重复 3 次 | 日志警告 |
| `critical` | 相同工具+参数重复 5 次 | 终止执行 |

## 5. 飞书凭证安全

### 凭证存储

- **App ID / App Secret** 存储在 `.env` 文件中
- `.env` 已加入 `.gitignore`，不会提交到版本控制
- 运行时通过环境变量读取

### 消息安全

- 内存 + 磁盘双重去重，防止重复处理
- 消息防抖合并，防止短时间内大量重复消息
- WebSocket 长轮询模式，无需暴露公网端口

## 6. 外部 JSON（MINIAGENT_CONFIG）与进程环境

**位置**: `miniagent/runtime/external_config.py`

为兼容 OpenClaw 等外部 JSON，进程启动时可设置环境变量 **`MINIAGENT_CONFIG`** 或 **`MINIAGENT_OPENCLAW_CONFIG`** 指向配置文件。解析后，若对应扁平环境变量尚未设置，会把 JSON 中的 **`apiKey` / `api_key`**、**`baseUrl`**、**`model`** 等写入 **`os.environ`**（例如 `OPENAI_API_KEY`），供 OpenAI 兼容客户端读取。

| 风险 | 缓解 |
|------|------|
| JSON 文件含明文密钥，权限过宽时同机用户可读 | 配置文件权限收紧（类 Unix：`chmod 600`）；勿将含真实密钥的 JSON 提交到 Git |
| 共享主机 / 多租户：子进程或库读取环境变量 | 优先使用 `.env` + 进程隔离；若必须用 JSON，部署后限制文件所有者 |
| 日志误带密钥 | 本仓库加载成功时仅记录**配置路径与模型名**，不记录 Key；仍应避免在自定义补丁或 `AGENT_DEBUG=true` 时把完整请求体写入日志 |
| 会话与记忆落盘 | 默认在 `MINI_AGENT_STATE`（常为 `workspaces/`）下写入 `sessions/`、`memory/` 等，可能含对话内容；备份与审计策略见 [DEPLOYMENT.md](DEPLOYMENT.md) |
| 定时任务表 | `scheduled_tasks/tasks.json` 保存各任务的 **prompt** 与元数据，可能含业务或隐私；勿提交版本库（默认路径见 [.gitignore](../.gitignore) 与 [ENGINEERING.md](ENGINEERING.md) §3.1） |

日常推荐：**主配置仍用 `.env`**，外部 JSON 仅作迁移或团队统一模型目录时的补充。

## 7. 数据安全原则

| 原则 | 实现 |
|------|------|
| 最小权限 | 工具只能访问白名单路径 |
| 会话隔离 | 每个 chat_id 独立队列和工作空间 |
| 输入验证 | 路径解析前验证，防止注入 |
| 输出截断 | LLM 响应和工具输出限制长度 |
| 密钥不落库 | 仓库内不出现真实 token；`.env` / 外部 JSON 不入版本控制 |
| 错误隔离 | 单个工具异常不影响 Agent 主流程 |

说明：密钥存在于**进程环境**或**本地配置文件**中属预期行为；「不写入日志」依赖默认日志字段与关闭过度调试。

## 8. 安全配置检查清单

- [ ] `.env` 文件权限设置为 600（仅所有者可读写）
- [ ] `.env` 已加入 `.gitignore`
- [ ] 若使用 `MINIAGENT_CONFIG`：JSON 文件权限 600，且不在备份中明文扩散
- [ ] `AGENT_DEBUG=false`（生产环境）
- [ ] 飞书应用已设置 IP 白名单（如适用）
- [ ] 工作空间目录权限正确；共享机上前缀 `MINI_AGENT_STATE` 到用户私有目录
- [ ] 定期检查 `workspaces/instances/` 无残留死实例

## 9. 相关文档

- [ENGINEERING.md](ENGINEERING.md)：`.env` 与密钥不入库、CI 质量门禁、`MINI_AGENT_STATE` 与 `workspaces/` 跟踪政策。
- [DEPLOYMENT.md](DEPLOYMENT.md)：安装、运行环境与故障排除。
- [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md)：多实例与磁盘注册目录语义。
