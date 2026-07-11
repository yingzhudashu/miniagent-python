# Mini Agent Python — 文档索引

> Mini Agent Python | 版本: 2.1.0 | 最后更新: 2026-07-11 | 与 `miniagent.__version__` 对齐 | 未发版行为以 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` 为准

---

## 新手快速路径

**5 分钟快速体验**：[USER_GUIDE.md §4 快速入门](USER_GUIDE.md#4-快速入门5分钟体验)

**第一次配置**：[USER_GUIDE.md §5 首次配置](USER_GUIDE.md#5-首次配置json-配置文件)

**CLI 命令速查**：[CLI.md](CLI.md)

**遇到问题**：[USER_GUIDE.md §17 FAQ](USER_GUIDE.md#17-常见问题faq) → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## 按角色导航

| 角色 | 建议路径 |
|------|----------|
| 新用户 | [USER_GUIDE.md](USER_GUIDE.md) → [README](../README.md) → [CLI.md](CLI.md) |
| 运维 | [DEPLOYMENT.md](DEPLOYMENT.md) → [TROUBLESHOOTING.md](TROUBLESHOOTING.md) → [FEISHU.md](FEISHU.md) |
| 架构师 | [ARCHITECTURE.md](ARCHITECTURE.md) → 专题文档 |
| 开发者 | [CONTRIBUTING.md](CONTRIBUTING.md)（含扩展开发与 API 示例）→ [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) |
| 维护者 | [ENGINEERING.md](ENGINEERING.md) §2 质量门禁 → [TEST_COVERAGE_MATRIX.md](TEST_COVERAGE_MATRIX.md) → [CHANGELOG](../CHANGELOG.md) |

---

## 功能清单

### 核心能力
- ✅ 多阶段智能（Phase 0-3）
- ✅ 三步需求澄清（Wittgenstein→Socrates→Polanyi）
- ✅ ReAct 循环（Think→Act→Observe）
- ✅ 三层记忆（短期/活动日志/语义检索）
- ✅ 双通道接入（CLI + 飞书）
- ✅ 定时任务（持久化 + 进程内调度）
- ✅ 多实例支持（注册表 + PID 清理）
- ✅ 自我优化（代码检查 + 优化提案）
- ✅ 沙箱安全（路径白名单 + 循环检测）

### 可选能力
- 🔌 技能系统（动态加载 + ClawHub 市场）
- 🔌 MCP 工具（Model Context Protocol）
- 🔌 联网搜索（Tavily API）
- 🔌 无头浏览器（Playwright）
- 🔌 飞书集成（IM + 云文档 + 多维表格）
- 🔌 知识库 RAG（`/kb` 挂载检索）

---

## SSOT 速查（单一事实来源）

| 主题 | 权威文档 |
|------|----------|
| 用户安装与配置 | [USER_GUIDE.md](USER_GUIDE.md) §3–5 |
| 通道绑定 | [FEISHU.md](FEISHU.md) §通道绑定 |
| 多实例 / `--stop` | [ENGINEERING.md](ENGINEERING.md) §3.3 |
| Trace 实现 | [ENGINEERING.md](ENGINEERING.md) §5 |
| 自我优化操作 | [SELF_OPT.md](SELF_OPT.md) |
| 输出格式 | [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md) |
| 提示词规范 | [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) |
| 环境变量分类 | [ENGINEERING.md](ENGINEERING.md) §1.2 |
| 知识库 / RAG | [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) |
| 完整 SSOT 表 | [ENGINEERING.md](ENGINEERING.md) §1 |

---

## 配置速查

| 配置项 | 说明 | 文档链接 |
|--------|------|----------|
| `secrets.openai_api_key` | LLM API 密钥 | [USER_GUIDE.md §5](USER_GUIDE.md) |
| `secrets.tavily_api_key` | 联网搜索 | [USER_GUIDE.md §12](USER_GUIDE.md#12-联网搜索与浏览器工具可选) |
| `feishu.*` | 飞书配置 | [FEISHU.md](FEISHU.md) |
| `agent.max_turns` | 执行轮数上限 | [ARCHITECTURE.md](ARCHITECTURE.md) |

---

## 测试与质量

- **测试数量**：以 `pytest tests/ --collect-only -q` 收集结果为准
- **覆盖率**：核心模块 ≥95%；整体 ≥80%（README badge 为整体指标）
- **测试矩阵**：[TEST_COVERAGE_MATRIX.md](TEST_COVERAGE_MATRIX.md)

```bash
pytest tests/ -q -m "not evaluation"
pytest tests/ --cov=miniagent --cov-report=html
```

---

## 文档分类

### 核心文档
[README](../README.md) · [USER_GUIDE.md](USER_GUIDE.md) · [CHANGELOG](../CHANGELOG.md)

### 用户与运维
[CLI.md](CLI.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [FEISHU.md](FEISHU.md)（含通道绑定） · [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)

### 架构与专题
[ARCHITECTURE.md](ARCHITECTURE.md) · [architecture.drawio](architecture.drawio) · [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) · [SECURITY.md](SECURITY.md) · [SELF_OPT.md](SELF_OPT.md) · [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)

### 性能
[PERFORMANCE.md](PERFORMANCE.md) — Part A 度量与测试 · Part B 运行时调优

### 开发者路径
[CONTRIBUTING.md](CONTRIBUTING.md)（Part 1 贡献 · Part 2 扩展 · Part 3 API）→ [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md)

### 维护者
[ENGINEERING.md](ENGINEERING.md)（§3.3 多实例注册表、§2 质量门禁）· [TEST_COVERAGE_MATRIX.md](TEST_COVERAGE_MATRIX.md)

---

## 项目结构

```
miniagent-python/
├── miniagent/             # 核心源码（17 个子包）
│   ├── cli/               # CLI 入口
│   ├── core/              # Agent 核心：任务分类、需求澄清、规划、执行、配置、LLM
│   ├── engine/            # 运行时引擎：主循环、命令调度、会话锁
│   ├── feishu/            # 飞书集成：IM、云文档、多维表格、卡片
│   ├── infrastructure/    # 基础设施：注册表、消息队列、日志、实例
│   ├── knowledge/         # 知识库管理：本地文档挂载与检索
│   ├── mcp/               # MCP 桥接（可选）
│   ├── memory/            # 三层记忆：会话、活动日志、语义检索
│   ├── runtime/           # 运行时上下文（RuntimeContext 组合根）
│   ├── scheduled_tasks/   # 定时任务：持久化 + 进程内 ticker
│   ├── security/          # 沙箱
│   ├── session/           # 会话管理
│   ├── skills/            # 技能加载、注册、ClawHub 客户端
│   ├── testing/           # 测试工具：测试运行器、类型定义
│   ├── tools/             # 工具实现：文件系统、网络、飞书、调度
│   ├── types/             # 类型定义（Pydantic / Protocol）+ 抽象接口层
│   └── utils/             # 共享工具函数
├── docs/                  # 文档（本目录）
├── tests/                 # pytest 测试
├── scripts/               # 维护脚本（见 scripts/README.md）
├── workspaces/            # 运行时状态（不入库）
│   ├── projects/          # 按 cwd 隔离的项目状态（sessions、memory、scheduled_tasks 等）
│   ├── instances/         # 多实例注册表（meta.json + heartbeat）
│   ├── logs/              # Trace 日志
│   └── skills/            # 技能包根
├── pyproject.toml         # 项目配置
├── config.defaults.json   # 默认配置（User/Advanced 分层）
└── README.md              # 项目介绍
```

---

## 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
