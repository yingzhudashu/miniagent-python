# Mini Agent Python — 文档索引

> 📅 最后更新: 2026-05-10 | 版本: 2.0.1（与 `miniagent.__version__` 对齐）

---

## 📖 快速开始

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [README](../README.md) | 项目介绍、快速上手 | 所有用户 |
| [CHANGELOG](../CHANGELOG.md) | 版本变更记录 | 所有用户 |

## 🏗️ 架构文档

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构总览 | 开发者、架构师 |
| [architecture.drawio](architecture.drawio) | 架构图 (draw.io) | 开发者 |
| [CHANNEL_BINDING.md](CHANNEL_BINDING.md) | 通道绑定功能详解 | 高级用户、开发者 |
| [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) | 三层记忆系统详解 | 开发者 |
| [SECURITY.md](SECURITY.md) | 安全模型说明 | 运维、开发者 |
| [CYBERNETICS_PLAN.md](CYBERNETICS_PLAN.md) | 控制论/自适应路线（规划稿，实验性） | 架构师 |

## 📦 模块文档

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [CLI.md](CLI.md) | CLI 命令手册 | 所有用户 |
| [FEISHU.md](FEISHU.md) | 飞书集成指南 | 运维、开发者 |
| [SELF_OPT.md](SELF_OPT.md) | 自我优化子系统 | 高级用户、开发者 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署指南 | 运维 |
| [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md) | 多实例注册表与磁盘布局 | 开发者、运维 |

## 🤝 参与贡献

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南、开发规范 | 贡献者 |

---

## 📁 项目结构

```
miniagent-python/
├── miniagent/                          # 源代码
│   ├── __main__.py               # 统一入口
│   ├── compat.py                 # 聚合导出与 unified_entry（原 unified 模块已移除）
│   ├── runtime/                  # RuntimeContext 组合根
│   ├── cli/                      # CLI 入口
│   ├── core/                     # 核心引擎 (规划 + 执行)
│   │   ├── agent.py              # Agent 编排
│   │   ├── planner.py            # Phase 1: 规划
│   │   ├── executor.py           # Phase 2: ReAct 执行
│   │   ├── config.py             # 配置管理
│   │   └── self_opt/             # 自我优化子系统
│   ├── engine/                   # 运行时引擎
│   │   ├── main.py               # 主启动入口
│   │   ├── engine.py             # UnifiedEngine
│   │   ├── command_dispatch.py   # 统一命令调度
│   │   ├── cli_commands.py       # CLI 命令实现
│   │   ├── feishu_runtime.py     # 飞书运行时
│   │   ├── session_lock.py       # 会话锁
│   │   ├── thinking.py           # 思考显示
│   │   ├── init.py               # 子系统初始化
│   │   └── welcome.py            # 欢迎界面
│   ├── feishu/                   # 飞书通信
│   │   ├── poll_server.py        # WebSocket 长轮询
│   │   ├── agent_handler.py      # 消息处理
│   │   ├── server.py             # HTTP Webhook
│   │   └── types.py              # 类型定义
│   ├── infrastructure/           # 基础设施
│   │   ├── registry.py           # 工具注册
│   │   ├── monitor.py            # 性能监控
│   │   ├── message_queue.py      # 消息队列
│   │   ├── channel_router.py     # 通道-会话路由器
│   │   ├── instance.py           # 多实例注册表
│   │   ├── logger.py             # 日志
│   │   ├── loop_detector.py      # 循环检测
│   │   └── process.py            # 进程管理
│   ├── memory/                   # 记忆系统
│   │   ├── context.py            # 上下文管理
│   │   ├── store.py              # 记忆存储
│   │   ├── activity_log.py       # 活动日志
│   │   └── keyword_index.py      # 语义检索
│   ├── session/                  # 会话管理
│   │   ├── manager.py            # 会话管理器
│   │   └── workspace.py          # 工作空间
│   ├── skills/                   # 技能系统
│   │   ├── registry.py           # 技能注册
│   │   ├── loader.py             # 技能加载
│   │   └── clawhub_client.py     # ClawHub 客户端
│   ├── tools/                    # LLM 可调用的工具
│   │   ├── exec.py               # 命令执行
│   │   ├── filesystem.py         # 文件操作
│   │   ├── web.py                # 网页访问
│   │   ├── skills.py             # 技能工具
│   │   └── self_opt.py           # 自优化工具
│   ├── security/                 # 安全层
│   │   └── sandbox.py            # 沙箱
│   └── types/                    # 类型定义
├── tests/                        # 单元测试
├── docs/                         # 文档
├── workspaces/                   # 运行时工作目录
└── README.md                     # 项目介绍
```

---

## 🔗 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
