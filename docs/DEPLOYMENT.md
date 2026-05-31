# 部署指南

> 模块: Mini Agent Python | 版本: 2.0.3（权威版本号见 `miniagent/__init__.py`）

## 环境要求

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.10+ | 与 `pyproject.toml` 中 `requires-python` 一致 |
| pip | 23+ | 包管理 |
| Git | 2.x | 版本控制（自我优化需要） |

### 核心 Python 依赖（随 `pip install -e .` 安装）

| 依赖 | 用途 |
|------|------|
| python-dotenv | 加载仓库根 `.env` |
| croniter / tzdata | 定时任务 cron 解析与时区（非 optional extra） |
| openai / pydantic 等 | 见 `pyproject.toml` `[project]` |

### 可选 pip extra

| extra | 用途 |
|-------|------|
| `feishu` | 飞书 SDK（`lark-oapi`）；启用 CLI+飞书 时安装 |
| `cli` | 终端 Rich Markdown 渲染 |
| `browser` | Playwright 无头浏览器（`browser_extract_text`） |
| `mcp` | 官方 MCP SDK（`MINIAGENT_MCP_STDIO`） |
| `dev` | pytest、ruff、pytest-cov |
| `typing` | mypy（与 CI `test` job 一致） |

完整列表见 [ENGINEERING.md](ENGINEERING.md) §1 与 [pyproject.toml](../pyproject.toml)。

## 安装步骤

### 1. 克隆项目

```bash
git clone https://github.com/yingzhudashu/miniagent-python.git
cd miniagent-python
```

### 2. 安装依赖

```bash
pip install -e .
# 开发（与默认 CI test job 一致：pytest、ruff、mypy 试点）：
pip install -e ".[dev,typing]"
# 飞书通道：
pip install -e ".[feishu]"
```

> 完整本地门禁命令见 [ENGINEERING.md](ENGINEERING.md) §2。

> 仓库 **不提供** 根目录 `requirements.txt`；依赖以 `pyproject.toml` 的 `[project]` / `[project.optional-dependencies]` 为准。

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```env
# === LLM 配置（必需） ===
OPENAI_API_KEY=sk-your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1    # 可选，兼容 API
OPENAI_MODEL=gpt-4o-mini                      # 默认模型

# === 飞书配置（可选） ===
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=your-app-secret

# === 调试配置（可选） ===
AGENT_DEBUG=false                             # 启用调试日志
MODEL_PROFILE=balanced                        # 模型预设
```

## 启动模式

### CLI 模式（默认）

```bash
python -m miniagent
```

进入交互式命令行，输入文字与 Agent 对话，使用 `.` 前缀命令管理系统。

### CLI + 飞书双通道

```bash
python -m miniagent --feishu
```

同时启动 CLI 交互和飞书 WebSocket 长连接，飞书消息和 CLI 共享 Agent 引擎。

### 运行时启用飞书

在 CLI 中输入：

```
.feishu start    # 启动飞书连接
.feishu stop     # 停止飞书连接
.feishu status   # 查看飞书状态
```

### 停止实例

```bash
python -m miniagent --stop           # 列出运行中实例；在终端中交互选择要停止的 ID
python -m miniagent --stop --all     # 停止全部
python -m miniagent --stop 1 2       # 停止指定实例 ID（非交互）
```

### 状态目录与多实例注册

- 默认将运行时状态写入当前工作目录下的 **`workspaces/`**（含 `instances/`、`sessions/` 等）。
- 设置 **`MINI_AGENT_STATE`** 可把整个状态根迁到其它路径（测试、多副本部署时常用）。
- 每次 **新进程注册实例前** 会清理磁盘上 **PID 已不存在** 的旧实例目录，**不会**误杀仍在运行的其它 Agent 进程。细节见 [ENGINEERING.md](ENGINEERING.md) §3.3。

对话历史、分层记忆、关键词索引、飞书去重状态等可能写入上述状态根下的子目录（含敏感业务内容）。
备份介质权限、共享主机上的路径隔离，见 [SECURITY.md](SECURITY.md)。

## 飞书配置

### 1. 创建飞书应用

1. 登录 [飞书开放平台](https://open.feishu.cn)
2. 创建企业自建应用
3. 获取 **App ID** 和 **App Secret**

### 2. 配置事件订阅

1. 在应用管理后台，进入「事件订阅」
2. 选择 **WebSocket 长连接模式**（无需公网 IP）
3. 订阅事件：`im.message.receive_v1`

### 3. 添加权限

| 权限 | 说明 |
|------|------|
| `im:message` | 接收和发送消息 |
| `im:message:send_as_bot` | 以 Bot 身份发送消息 |

### 4. 发布应用

应用创建后需要发布才能接收消息。

## 部署场景

### 本地开发

```bash
python -m miniagent              # 仅 CLI
python -m miniagent --feishu     # CLI + 飞书
```

### 家庭服务器 / NAS

WebSocket 长连接模式**无需公网 IP**，适合内网部署：

```bash
# 使用 nohup 后台运行
nohup python -m miniagent --feishu > agent.log 2>&1 &

# 或使用 systemd（Linux）
# 参见下方 systemd 配置示例
```

### systemd 服务配置（Linux）

```ini
[Unit]
Description=Mini Agent Python
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/miniagent-python
ExecStart=/usr/bin/python3 -m miniagent --feishu
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Windows 服务

可使用 NSSM 或 Task Scheduler：

```powershell
# Task Scheduler 方式
$action = New-ScheduledTaskAction -Execute "python" -Argument "-m miniagent --feishu" -WorkingDirectory "C:\path\to\miniagent-python"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName "MiniAgent" -Action $action -Trigger $trigger
```

## 多实例部署

Mini Agent 支持多实例并行运行：

- 每个实例通过 `workspaces/instances/<id>/meta.json` 注册
- `register()` / `list_all()` 按 **操作系统 PID 是否仍存在** 清理僵尸注册目录；心跳文件仅作观测，**不作为**存活判定（详见 [ENGINEERING.md](ENGINEERING.md) §3.3）
- 同一会话通过 `.lock` 文件互斥，防止并发冲突

```bash
# 终端 1
python -m miniagent                    # 实例 #1 (CLI)

# 终端 2
python -m miniagent --feishu           # 实例 #2 (CLI + 飞书)
```

管理实例：

```
.instance list                   # 列出所有实例
.instance stop 2                 # 停止实例 #2
```

## 定时任务与状态

用户配置的 **周期性 / 一次性 Agent 回合** 持久化在状态根下：

| 路径 | 说明 |
|------|------|
| `{MINI_AGENT_STATE}/scheduled_tasks/tasks.json` | 任务定义（含 **prompt**，可能含业务隐私） |
| `scheduled_tasks/*.lock` | 调度与单任务互斥锁（见 [ENGINEERING.md](ENGINEERING.md) §3.3） |

- **依赖**：`croniter`、`tzdata` 已包含在主包 `[project]` 依赖中，无需单独 extra。
- **运维环境变量**：
  - `MINIAGENT_DISABLE_SCHEDULED_TASKS=1` — 关闭后台 ticker（不删除磁盘任务表）
  - `MINIAGENT_SCHEDULE_DISPATCH_BACKOFF` — dispatch 失败时推迟 `next_run_at` 的秒数（默认 60）
  - `MINIAGENT_TIMEZONE` / `TZ` — 进程默认 IANA 时区（Agent、`get_time`、新建定时任务默认）；修改 `.env` 后须**重启进程**（Windows 上尤其重要）
- **用户操作**：CLI `.schedule`（含 **`align-tz`** 将遗留 `timezone: UTC` 对齐到 env）、Agent 工具 `manage_scheduled_task`（`align_tz`）；飞书侧通常仅 `list` / `show`。详见 [USER_GUIDE.md](USER_GUIDE.md) §8、[ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」。

## 监控和日志

### 日志文件

| 路径 | 内容 |
|------|------|
| `workspaces/memory/YYYY-MM-DD.md` | 活动日志（Layer 2） |
| 标准输出 | 实时交互日志 |

### Agent 状态检查

```
.status                          # 检查 Agent 是否卡死
.stats                           # 工具调用统计
.queue status                    # 消息队列状态
```

## 备份

关键数据目录：

| 目录 | 说明 | 备份建议 |
|------|------|---------|
| `workspaces/sessions/` | 会话历史和配置 | 定期备份 |
| `workspaces/scheduled_tasks/` | 定时任务表（含 prompt） | 与 sessions 同级敏感，定期备份 |
| `workspaces/memory/` | 活动日志 | 按需备份 |
| `workspaces/skills/` | 已安装技能 | 可重新安装 |
| `.env` | 环境配置 | 必须备份（含密钥） |

## 故障排除

| 问题 | 解决方案 |
|------|---------|
| 飞书连接失败 | 检查 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` |
| LLM 调用超时 | 检查 `OPENAI_API_KEY` 和网络连接 |
| 会话锁冲突 | 运行 `python -m miniagent --stop` 清理 |
| Agent 卡死 | 使用 `.status` 检查，或 `.stop` 重启 |
| 编码问题 | 确保 `PYTHONIOENCODING=utf-8` |

## 相关文档

- [ENGINEERING.md](ENGINEERING.md)：CI 与本地质量门禁、`MINI_AGENT_STATE` 与仓库卫生约定。
- [SECURITY.md](SECURITY.md)：沙箱与密钥处理。
- [ENGINEERING.md](ENGINEERING.md) §3.3：多实例与 `--stop` 行为。
- [USER_GUIDE.md](USER_GUIDE.md) §8：定时任务用户说明。
