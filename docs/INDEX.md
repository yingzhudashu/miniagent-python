# Mini Agent Python — 文档索引

> 📅 最后更新: 2026-05-11 | 版本: 2.0.2（与 `miniagent.__version__` 对齐）

---

## 📖 快速开始

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| **[USER_GUIDE.md](USER_GUIDE.md)** | **零基础全项目使用指南**（安装、配置、日常、FAQ、安全） | **所有用户；新手首选** |
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
| [ENGINEERING.md](ENGINEERING.md) | 仓库卫生、质量门禁、单一事实来源 | 维护者、CI 负责人 |

---

## 📁 项目结构

**权威目录树（与仓库同步）**；README 中仅为缩略指引，详见本段。

```
miniagent-python/
├── miniagent/
│   ├── __main__.py               # 统一入口（.env、--stop → compat.unified_entry）
│   ├── compat.py                 # 聚合导出与 unified_entry
│   ├── runtime/
│   │   ├── context.py            # RuntimeContext 组合根
│   │   └── external_config.py    # MINIAGENT_CONFIG 等外部 JSON 加载与补丁
│   ├── cli/
│   │   └── cli.py                # console_scripts 入口 → __main__
│   ├── core/
│   │   ├── agent.py              # 两阶段编排入口 run_agent / run_pipeline
│   │   ├── planner.py            # Phase 1 结构化规划
│   │   ├── executor.py           # Phase 2 ReAct / 分步执行
│   │   ├── config.py             # AgentConfig、MODEL_PROFILES
│   │   ├── openai_client.py      # 共享 AsyncOpenAI
│   │   ├── llm_params.py         # 完成参数与 thinking 合并
│   │   ├── thinking_presets.py   # 业务深度 → 档位映射
│   │   ├── task_classifier.py    # 任务难度预分类
│   │   ├── vendor/qwen_extra.py  # DashScope/Qwen extra_body
│   │   └── self_opt/             # 自我优化（inspector、proposal、git 等）
│   ├── engine/
│   │   ├── main.py               # unified_main、CLI 主循环
│   │   ├── engine.py             # UnifiedEngine
│   │   ├── init.py               # 内置工具、技能、MCP、SessionManager
│   │   ├── builtin_tools.py      # ALL_TOOLS 注册
│   │   ├── command_dispatch.py   # `.` 命令调度
│   │   ├── cli_commands.py       # 命令实现
│   │   ├── cli_state.py          # CliLoopState 与主循环状态对齐
│   │   ├── feishu_state.py       # FeishuRuntime
│   │   ├── feishu_runtime.py     # 兼容别名：重导出 FeishuRuntime
│   │   ├── session_lock.py
│   │   ├── thinking.py           # ThinkingDisplay
│   │   └── welcome.py
│   ├── feishu/
│   │   ├── poll_server.py
│   │   ├── agent_handler.py
│   │   ├── server.py
│   │   └── types.py
│   ├── infrastructure/
│   │   ├── registry.py           # ToolRegistry
│   │   ├── monitor.py
│   │   ├── message_queue.py
│   │   ├── channel_router.py
│   │   ├── instance.py
│   │   ├── feishu_inbound_lock.py
│   │   ├── tracing.py
│   │   ├── logger.py
│   │   ├── loop_detector.py
│   │   └── process.py
│   ├── memory/
│   │   ├── defaults.py           # 进程默认记忆 bundle / MINI_AGENT_STATE
│   │   ├── context.py            # 消息窗口、压缩、记忆注入
│   │   ├── store.py
│   │   ├── activity_log.py
│   │   ├── keyword_index.py
│   │   ├── memory_pipeline.py    # 记忆注入管线
│   │   ├── history_archive.py
│   │   ├── history_bridge.py
│   │   ├── layered_memory.py
│   │   └── dream_scheduler.py
│   ├── session/
│   │   ├── manager.py
│   │   └── workspace.py
│   ├── skills/
│   │   ├── registry.py
│   │   ├── loader.py
│   │   ├── paths.py              # 技能根目录解析
│   │   ├── builtin_toolboxes.py
│   │   └── clawhub_client.py
│   ├── tools/
│   │   ├── exec.py
│   │   ├── filesystem.py
│   │   ├── web.py
│   │   ├── skills.py
│   │   ├── self_opt.py
│   │   ├── git_readonly.py
│   │   └── session_memory.py     # 会话记忆类工具（init 注册）
│   ├── mcp/                      # 可选：stdio MCP → mcp_* 工具
│   │   ├── bridge.py
│   │   └── runtime.py
│   ├── security/
│   │   └── sandbox.py
│   └── types/                    # Pydantic / Protocol（__init__.py 聚合导出）
├── scripts/                      # 维护脚本（如 bootstrap_clawhub_skills.py）
├── tests/
├── docs/
│   └── examples/                 # 脱敏配置片段（见 examples/README.md）
├── workspaces/                   # 默认状态目录（可用 MINI_AGENT_STATE 迁出）
└── README.md
```

**`workspaces/` 与 Git**：`.gitignore` 已忽略 `instances/`、`sessions/`、`memory/`、`keyword-index.json`、`feishu/`、性能日志、飞书路由 JSON 等运行时产物；结构说明见 [ENGINEERING.md](ENGINEERING.md) §3.1。本地与 CI 推荐设置 `MINI_AGENT_STATE` 将状态迁出仓库；需要提交**脱敏**结构示例时请放在 [examples/](examples/)（说明见 [examples/README.md](examples/README.md)），勿将含密钥或真实对话的文件放在 `workspaces/` 并尝试提交。

---

## 🔗 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
