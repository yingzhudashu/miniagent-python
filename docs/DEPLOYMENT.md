# 部署指南

> Mini Agent Python | 版本: 2.2.0 | 最后更新: 2026-07-14 | 与 `miniagent.__version__` 对齐

## 环境要求

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.10+ | 与 `pyproject.toml` 中 `requires-python` 一致 |
| pip | 23+ | 包管理 |
| Git | 2.x | 可选；自我优化、技能 vendor 等 Git 工作流需要 |

核心依赖与可选 pip extra 的完整列表见 **[README.md](../README.md) §安装** 与 [pyproject.toml](../pyproject.toml)；本文不再重复 extra 表。

## 安装

首次安装（克隆、虚拟环境、`pip install -e .`、创建 `config.user.json`）见 **[README.md](../README.md) §安装** 与 **§配置**。

贡献者开发安装（`pip install -e ".[dev,typing]"` 等）见 **[CONTRIBUTING.md](CONTRIBUTING.md) §开发环境设置**；可选 pip extra 与 Python 版本要求见上文「环境要求」表。

## 启动模式

CLI 交互启动、`--feishu` 双通道、`--stop` 停止实例等命令与首次使用说明见 **[README.md](../README.md) §启动与退出**。

运维场景补充：

- **后台运行**（家庭服务器 / NAS）：WebSocket 长连接**无需公网 IP**，可用 `nohup` 或 systemd（见下文示例）。
- **多实例**：不同项目目录可并行；同一 cwd 第二次启动会被拒绝。注册表与 `--stop` 语义见 [ENGINEERING.md](ENGINEERING.md) §3.3。

### 状态目录与多实例注册

项目数据默认写入 **`workspaces/projects/{project_key}/`**；实例注册表固定在 miniagent 包根的 **`workspaces/instances/`**（与 cwd 无关）。`MINIAGENT_PATHS_STATE_DIR` 仅覆盖项目 workspace 根，**不改变**注册表位置。同一 cwd 仅允许一个存活实例；多项目并行请换目录启动。完整路径布局、PID 存活判定、`--stop` 与 `--state-dir` 语义见 **[ENGINEERING.md §3.3](ENGINEERING.md#33-多实例注册表)**（canonical 路径见 §3）。

对话历史、记忆、飞书去重等可能写入状态根子目录（含敏感内容）；备份与共享主机隔离见 [SECURITY.md](SECURITY.md)。

## 飞书配置

飞书应用创建、事件订阅、权限与发布步骤见 **[FEISHU.md](FEISHU.md) §快速开始**。部署侧仅需：

1. 在 `config.user.json` 的 `secrets` 中填写 `feishu_app_id` / `feishu_app_secret`
2. 安装可选依赖：`pip install -e ".[feishu]"`
3. 启动：`python -m miniagent --feishu`

运维速查与 WebSocket 排障见 [FEISHU.md](FEISHU.md) §运维速查。

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
nohup python -m miniagent --feishu > miniagent-stdout.log 2>&1 &

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

不同 cwd 可并行；同一 cwd 仅一实例。管理：`python -m miniagent --stop`、`/instance list`、`/instance stop <id>`。完整语义见 **[ENGINEERING.md §3.3](ENGINEERING.md#33-多实例注册表)**。

## 定时任务与状态

定时任务定义持久化在 **`{paths.state_dir}/scheduled_tasks/tasks.json`**[^paths]。用户操作：CLI **`/schedule`**（语法见 [CLI.md](CLI.md)）；运维 env（`MINIAGENT_DISABLE_SCHEDULED_TASKS` 等）见 [ENGINEERING.md §1.2](ENGINEERING.md#12-环境变量分类)。架构数据流与用户要点见 [ARCHITECTURE.md「定时任务子系统」](ARCHITECTURE.md#定时任务子系统)、[USER_GUIDE.md §3](USER_GUIDE.md#3-定时任务)。

[^paths]: canonical 路径布局见 [ENGINEERING.md §3](ENGINEERING.md#3-状态目录与测试隔离)。

## 监控和日志

### 日志文件

| 路径 | 内容 |
|------|------|
| `{paths.state_dir}/memory/YYYY-MM-DD.md`[^paths] | 活动日志（Layer 2） |
| 标准输出 | 实时交互日志 |

### Agent 状态检查

```
/status                          # 检查 Agent 是否卡死
/stats                           # 工具调用统计
/queue status                    # 消息队列状态
```

## 备份

关键数据目录：

| 目录 | 说明 | 备份建议 |
|------|------|---------|
| `{paths.state_dir}/sessions/`[^paths] | 会话历史和配置 | 定期备份 |
| `{paths.state_dir}/scheduled_tasks/` | 定时任务表（含 prompt） | 与 sessions 同级敏感，定期备份 |
| `{paths.state_dir}/memory/` | 活动日志 | 按需备份 |
| `workspaces/skills/` | 已安装技能 | 可重新安装 |
| `config.user.json` | 用户配置与密钥 | 必须备份（含密钥） |

## 故障排除

| 问题 | 解决方案 |
|------|---------|
| 飞书连接失败 | 优先检查 `config.user.json` 的 `secrets.feishu_app_id` / `secrets.feishu_app_secret`；详见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) §飞书集成问题 |
| LLM 调用超时 | 优先检查 `config.user.json` 的 `secrets.openai_api_key` 与网络连接 |
| 会话锁冲突 | 运行 `python -m miniagent --stop` 清理 |
| Agent 卡死 | 使用 `/status` 检查；`Ctrl+C` 结束进程后重新 `python -m miniagent` 启动 |
| 编码问题 | 确保 `PYTHONIOENCODING=utf-8` |

## 相关文档

- [ENGINEERING.md](ENGINEERING.md)：CI 与本地质量门禁、`MINIAGENT_PATHS_STATE_DIR` 与仓库卫生约定；§3.3 多实例与 `--stop`。
- [SECURITY.md](SECURITY.md)：沙箱与密钥处理。
- [USER_GUIDE.md](USER_GUIDE.md) §3：定时任务用户说明。
