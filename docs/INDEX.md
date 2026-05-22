# Mini Agent Python — 文档索引

> 📅 最后更新: 2026-05-23 | 版本: 2.0.2（与 `miniagent.__version__` 对齐；未发版行为以 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` 为准）

---

## 📖 快速开始

| 角色 | 建议路径 |
|------|----------|
| 新用户 | [USER_GUIDE.md](USER_GUIDE.md) → [README](../README.md) 快速上手 → [CLI.md](CLI.md) |
| 开发者 | [ARCHITECTURE.md](ARCHITECTURE.md) → 专题文档 |
| 维护者 | [ENGINEERING.md](ENGINEERING.md) §5 文档维护清单 → [CHANGELOG](../CHANGELOG.md) |

## 📚 文档分类

**核心文档**：[README](../README.md) · [USER_GUIDE.md](USER_GUIDE.md) · [CHANGELOG](../CHANGELOG.md)

**架构**：[ARCHITECTURE.md](ARCHITECTURE.md) · [architecture.drawio](architecture.drawio) · [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) · [SECURITY.md](SECURITY.md)

**功能指南**：[CLI.md](CLI.md) · [FEISHU.md](FEISHU.md) · [CHANNEL_BINDING.md](CHANNEL_BINDING.md) · [SELF_OPT.md](SELF_OPT.md)

**运维**：[DEPLOYMENT.md](DEPLOYMENT.md) · [ENV_REFERENCE.md](ENV_REFERENCE.md) · [PERFORMANCE.md](PERFORMANCE.md)

**开发**：[CONTRIBUTING.md](CONTRIBUTING.md) · [ENGINEERING.md](ENGINEERING.md)（含多实例注册表、离线测评）

**探索性**：[CYBERNETICS_PLAN.md](CYBERNETICS_PLAN.md) — Draft / Exploratory

## 📁 项目结构

```
miniagent-python/
├── miniagent/             # 核心源码（16 个子包）
│   ├── cli/               # CLI 入口
│   ├── core/              # Agent 核心：规划、执行、配置、LLM
│   ├── engine/            # 运行时引擎：主循环、命令调度、会话锁
│   ├── feishu/            # 飞书集成：IM、云文档、多维表格、卡片
│   ├── infrastructure/    # 基础设施：注册表、消息队列、日志、实例
│   ├── mcp/               # MCP 桥接（可选）
│   ├── memory/            # 三层记忆：会话、活动日志、语义检索
│   ├── runtime/           # 运行时上下文（RuntimeContext 组合根）
│   ├── scheduled_tasks/   # 定时任务：持久化 + 进程内 ticker
│   ├── security/          # 沙箱
│   ├── session/           # 会话管理
│   ├── skills/            # 技能加载、注册、ClawHub 客户端
│   ├── tools/             # 工具实现：文件系统、网络、飞书、调度
│   └── types/             # 类型定义（Pydantic / Protocol）
├── docs/                  # 文档
├── tests/                 # pytest 测试
├── scripts/               # 维护脚本
├── workspaces/            # 运行时状态（不入库）
│   └── skills/            # 技能包根（基线模板在 miniagent/skills/templates/）
├── pyproject.toml         # 项目配置
└── README.md              # 项目介绍
```

## 🔗 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
