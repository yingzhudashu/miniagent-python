# Mini Agent Python

基于 LLM 的两阶段智能代理系统。支持 CLI 和飞书双通道接入。

## 特性

- **两阶段架构**: Plan（规划）→ Execute（执行），精确控制工具调用
- **ReAct 循环**: Think → Act → Observe，多轮推理直到任务完成
- **三层记忆**: 短期记忆 / 活动日志 / 语义检索
- **双通道接入**: 同一进程内 CLI 主循环 + 可选飞书 WebSocket 长轮询（无单独「纯飞书」入口）
- **消息队列**: queue（按序）/ preemptive（打断）双模式
- **多实例**: 注册表 + 心跳，支持多终端并行
- **可插拔技能**: 动态加载，ClawHub 技能市场
- **自我优化**: 代码检查 + 优化提案 + Git 快照
- **沙箱安全**: 路径白名单 + 循环检测 + 权限控制

## 快速开始

```bash
# 安装（<repo-url> 为占位符，请换为实际远程；fork 说明见 docs/CONTRIBUTING.md）
git clone <repo-url>
cd miniagent-python
pip install -e ".[dev]"              # 开发：pytest / ruff
# pip install -e ".[dev,feishu]"    # 若需本地跑通飞书 SDK 相关路径
# 仅需运行时：pip install -e .
cp .env.example .env       # 编辑填入 OPENAI_API_KEY

# 可选：将状态目录迁出仓库（测试 / 多副本部署）
# PowerShell: $env:MINI_AGENT_STATE = "$env:TEMP\miniagent-state"
# bash: export MINI_AGENT_STATE=/tmp/miniagent-state

# 启动
python -m miniagent                  # CLI 模式
python -m miniagent --feishu         # CLI + 飞书
python -m miniagent --stop           # 列出实例；交互停止 / --stop --all / --stop <id>...
```

新进程注册时会自动删除磁盘上 **PID 已退出** 的旧实例注册目录，**不会**终止仍在运行的其它 Agent。详见 [docs/INSTANCE_REGISTRY.md](docs/INSTANCE_REGISTRY.md)。

## 常用命令

| 命令 | 说明 |
|------|------|
| `.status` | 检查 Agent 状态（不中断执行） |
| `.session list` | 列出所有会话 |
| `.session switch <id>` | 切换会话 |
| `.instance list` | 列出运行实例 |
| `.feishu start/stop` | 飞书控制 |
| `.queue status` | 消息队列状态 |
| `.help` | 显示完整帮助 |

> 所有 `.` 命令在 CLI 和飞书中均可使用。

## 项目结构

```
miniagent/
├── __main__.py     # 进程入口（.env、--stop、委托 compat）
├── compat.py       # 聚合导出与 unified_entry（组装 RuntimeContext）
├── runtime/        # RuntimeContext 组合根
├── cli/            # 控制台脚本 miniagent 的入口
├── core/           # 核心引擎 (agent, planner, executor, openai_client, self_opt)
├── engine/         # 运行时编排 (main, engine, commands, feishu_state)
├── feishu/         # 飞书通信 (WebSocket, handler)
├── infrastructure/ # 基础设施 (registry, queue, monitor, instance, channel_router)
├── memory/         # 三层记忆 (store, context, index, defaults)
├── session/        # 会话管理 (manager, workspace)
├── skills/         # 技能系统 (registry, loader, clawhub)
├── tools/          # LLM 工具 (exec, filesystem, web)
├── security/       # 安全沙箱
└── types/          # 类型定义
```

## 文档

| 文档 | 说明 |
|------|------|
| [docs/INDEX.md](docs/INDEX.md) | 文档索引 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构 |
| [docs/CLI.md](docs/CLI.md) | CLI 命令手册 |
| [docs/FEISHU.md](docs/FEISHU.md) | 飞书集成 |
| [docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md) | 三层记忆系统 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 部署指南 |
| [docs/SECURITY.md](docs/SECURITY.md) | 安全模型 |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | 贡献指南 |
| [docs/ENGINEERING.md](docs/ENGINEERING.md) | 软件工程实践与质量门禁 |
| [docs/INSTANCE_REGISTRY.md](docs/INSTANCE_REGISTRY.md) | 多实例注册与清理语义 |
| [docs/SELF_OPT.md](docs/SELF_OPT.md) | 自我优化 |

## 测试

```bash
python -m pytest tests/ -v       # 约 119 tests（以 pytest 收集为准）
python -m ruff check miniagent tests
python -m compileall -q miniagent
```

## 技术栈

- Python 3.10+
- OpenAI API (GPT-4o-mini)
- 飞书 SDK (lark-oapi, WebSocket)
- pytest (单元测试)

## License

MIT
