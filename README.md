# Mini Agent Python

![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-2.1.0-blue)
![Tests](https://img.shields.io/badge/tests-dynamic-blue)
> **测试数量**：以 `pytest --collect-only -q` 为准，不硬编码以避免漂移（见 [CONTRIBUTING.md](docs/CONTRIBUTING.md) §文档与版本对齐清单）
![Coverage](https://img.shields.io/badge/coverage-%E2%89%A580%25%20%E7%9B%AE%E6%A0%87-yellow)
> **覆盖率**：整体 ≥80%、核心模块 ≥95%；本地 `pytest --cov=miniagent` 验证，详见 [INDEX.md](docs/INDEX.md) §测试与质量

基于 LLM 的多阶段智能代理系统。支持 CLI 和飞书双通道接入，具备记忆、定时任务、技能与自我优化能力。

## 项目简介

**Mini Agent Python** 是在本地（或你自己的服务器）上运行的智能助手程序。它通过大语言模型（LLM）理解文字需求，并在授权范围内调用工具（读写文件、执行命令、联网搜索等）自动完成任务。

与「只能聊天」的网页机器人相比：

| 能力 | 说明 |
|------|------|
| **多阶段** | 先需求澄清（三步法）再规划再执行；简单任务可跳过规划直接执行。详见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。 |
| **工具** | 模型可调用注册的工具；未配置联网密钥不会偷偷联网。 |
| **CLI + 可选飞书** | 默认在终端对话；也可同一进程挂上飞书机器人（WebSocket），无「只飞书无终端」形态。 |
| **会话与记忆** | 多会话隔离，支持跨会话记忆与检索；见 [MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md)。 |

**不适合的场景**：不要当作对公网匿名用户开放的多租户服务；部署与安全边界见 [SECURITY.md](docs/SECURITY.md)、[DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 功能清单

### 核心能力

- ✅ 多阶段智能（Phase 0 → 0.5 → 1 → 2）
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

## 架构概览

Mini Agent Python 采用 **多阶段架构**（Phase 0 分类 → Phase 0.5 需求澄清 → Phase 1 规划 → Phase 2 执行），通过 **ReAct 循环** 驱动 LLM 调用工具完成任务。系统分为 **12 个功能层**，支持 **CLI + 飞书** 双通道，经 **ChannelRouter** 实现通道绑定与会话共享。

```
用户 → CLI / 飞书 WebSocket → 通道适配 → 应用用例 → 引擎 → 核心（澄清→规划→执行）
                            ↕ 标准消息契约          ↓
                         组合根/基础设施 ← 工具层 + 记忆层 → 安全/类型
```

CLI、飞书文本、媒体和定时任务统一使用平台无关 `InboundMessage`；CLI、飞书和定时结果统一使用 `OutboundEvent` 经 `ChannelRegistry` 投递。唯一 `ApplicationContainer` 由正式入口构造，长期服务全部由 `LifecycleManager` 管理；实例 heartbeat 仅用于存活诊断，不属于 Agent 消息通道。

完整分层说明、数据流与扩展点见 **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**。`miniagent/` 下 20 个物理子包见 [项目结构](#项目结构)。

**与 OpenClaw 的关系**：[OpenClaw](https://docs.openclaw.ai) 是自托管 Gateway，将多种渠道接到 Agent；本仓库是 **Python Agent 核心**（多阶段架构、ReAct、技能与 ClawHub、飞书与 CLI、本地记忆），可与 ClawHub 技能生态对齐，但不是 OpenClaw Gateway 的等价实现。

## 环境要求

| 依赖 | 要求 | 说明 |
|------|------|------|
| Python | 3.10+ | 与 `pyproject.toml` 中 `requires-python` 一致 |
| pip | 23+ | 包管理 |
| 网络 | 可访问 LLM 服务商 | 安装依赖与 API 调用需要 |
| 终端 | PowerShell / bash 等 | Windows、macOS、Linux 均可 |
| Git | 2.x（可选） | 克隆仓库时需要；自我优化 / 技能 vendor 等 Git 工作流需要；ZIP 解压亦可 |

至少准备一种 **LLM API**（OpenAI 兼容接口）。密钥只应出现在本机 `config.user.json` 的 `secrets` 部分，勿泄露。可选：飞书应用、Tavily 搜索、Playwright、MCP 服务——用到再配置。飞书步骤见 [FEISHU.md](docs/FEISHU.md)；部署环境检查见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 安装

### 获取代码

```bash
git clone <仓库地址>
cd miniagent-python
```

若无 Git，下载源码 ZIP 解压后 `cd` 到目录即可。

### 虚拟环境（建议）

**Windows（PowerShell）**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
```

### 安装本项目

**仅运行（最小）**

```bash
pip install -e .
```

**开发（含测试与静态检查，与默认 CI 一致）**

```bash
pip install -e ".[dev,typing]"
```

**可选 pip extra**（与 [`pyproject.toml`](pyproject.toml) 一致；权威说明见 [ENGINEERING.md](docs/ENGINEERING.md) §1）：

| extra | 用途 |
|-------|------|
| `feishu` | 飞书 SDK（`lark-oapi`） |
| `cli` | 终端 Rich Markdown 渲染 |
| `browser` | Playwright 无头浏览器 |
| `mcp` | 官方 MCP SDK |
| `dev` | pytest、ruff、pytest-cov |
| `typing` | mypy（与 CI `test` job 一致） |

可同时叠加：`pip install -e ".[dev,feishu]"` 等。

启动方式（等价）：

```bash
python -m miniagent
# 或（PATH 含脚本目录时）
miniagent
```

## 快速入门

若已完成安装，按以下步骤 5 分钟体验：

### 最小配置

首次启动会进入交互式配置引导并生成 `config.user.json`。也可以手动创建该文件并填入 API 密钥：

```json
{
  "secrets": {
    "openai_api_key": "你的密钥"
  }
}
```

### 启动并对话

```bash
python -m miniagent
```

看到 `>>>` 提示符后输入：

```
帮我列出当前目录的文件
```

Agent 会自动调用文件工具完成任务。

## 配置

用户配置位于项目根目录的 `config.user.json`；缺失时由首次启动引导创建。

**不要** 把含真实密钥的 `config.user.json` 提交到公开仓库（已在 `.gitignore` 中忽略）。

### 必填（最小可运行）

```json
{
  "secrets": {
    "openai_api_key": "sk-your-api-key"
  },
  "model": {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini"
  }
}
```

| 字段 | 含义 |
|------|------|
| `secrets.openai_api_key` | API 密钥（勿在截图中泄露） |
| `model.base_url` | 可选，兼容网关地址 |
| `model.model` | 可选，模型 id |

### 常用可选配置

完整默认值见 [`miniagent/resources/config.defaults.json`](miniagent/resources/config.defaults.json)：

| 配置路径 | 用途 |
|----------|------|
| `model.temperature` | 模型温度，默认 0.7 |
| `model.thinking_level` | 思考档位：`light` / `medium` / `heavy` |
| `agent.max_turns` | 单轮 ReAct 最大轮数，默认 400 |
| `agent.debug` | `true` 时更啰嗦的日志 |
| `secrets.tavily_api_key` | 启用联网搜索（Tavily） |
| `secrets.feishu_app_id` / `secrets.feishu_app_secret` | 飞书应用凭证（事件订阅另需 `feishu_verification_token` 等，见 [FEISHU.md](docs/FEISHU.md)） |
| `paths.state_dir` | 状态根目录，默认 `workspaces`（canonical 布局：`workspaces/projects/{project_key}/`，见 [ENGINEERING.md](docs/ENGINEERING.md) §3） |

**配置分层**：包内 `miniagent/resources/config.defaults.json` 顶部 `_config_guide` 列出 User 层与 Advanced 层。普通用户只需在 `config.user.json` 覆盖 User 层；Advanced 节（`memory`、`trace` 等）一般保持默认。优先级：**config.user.json > 包内 defaults**。运维/调试类环境变量（如 `MINIAGENT_PATHS_STATE_DIR`、`AGENT_DEBUG`）见 [ENGINEERING.md](docs/ENGINEERING.md) §1.2。

## 启动与退出

### 仅终端（CLI）

```bash
python -m miniagent
python -m miniagent --continue          # 继续上次 CLI 会话
python -m miniagent --session <会话ID>  # 指定会话
```

看到欢迎信息后直接输入自然语言需求。退出：`/exit`、`quit`、`exit` 或 `Ctrl+D`。

### 终端 + 飞书

需已安装 `[feishu]` 并配置飞书凭证：

```bash
python -m miniagent --feishu
python -m miniagent --feishu --continue
```

飞书不会单独占无终端进程；始终与 CLI 主循环一起。详见 [FEISHU.md](docs/FEISHU.md)。

### 多实例与停止

```bash
python -m miniagent --stop
```

列出本机已注册实例并交互停止；`--stop --all` 或 `--stop 1 2` 等用法见 [ENGINEERING.md](docs/ENGINEERING.md) §3.3。同一 cwd 第二次启动会被拒绝，需先 `--stop`。

## 常用命令

入门：`/help` · `/status` · `/session list` · `/doctor` · `/exit`

完整点命令手册见 **[CLI.md](docs/CLI.md)**；日常使用与专题配置见 **[USER_GUIDE.md](docs/USER_GUIDE.md)**。

## 按角色阅读

| 角色 | 建议路径 |
|------|----------|
| 新用户 | 本文 → [USER_GUIDE.md](docs/USER_GUIDE.md) → [CLI.md](docs/CLI.md) |
| 运维 | [DEPLOYMENT.md](docs/DEPLOYMENT.md) → [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) → [FEISHU.md](docs/FEISHU.md) |
| 架构师 | 本文 §架构概览 → [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 开发者 | [CONTRIBUTING.md](docs/CONTRIBUTING.md) → [PROMPT_GUIDELINES.md](docs/PROMPT_GUIDELINES.md) |
| 维护者 | [ENGINEERING.md](docs/ENGINEERING.md) → [TEST_COVERAGE_MATRIX.md](docs/TEST_COVERAGE_MATRIX.md) → [CHANGELOG.md](CHANGELOG.md) |

## 配置速查

| 配置项 | 说明 | 专题文档 |
|--------|------|----------|
| `secrets.openai_api_key` | LLM API 密钥 | 本文 §配置 |
| `secrets.tavily_api_key` | 联网搜索 | [USER_GUIDE.md §6](docs/USER_GUIDE.md#6-联网搜索与浏览器工具可选) |
| `feishu.*` | 飞书配置 | [FEISHU.md](docs/FEISHU.md) |
| `agent.max_turns` | 执行轮数上限 | [ARCHITECTURE.md](docs/ARCHITECTURE.md) |

## 项目结构

```
miniagent-python/
├── miniagent/             # 核心源码（20 个子包）
│   ├── application/       # 平台无关用例协调、通道注册与出站分发
│   ├── bootstrap/         # 服务生命周期、生产图装配与启动回滚
│   ├── cli/               # CLI 入口
│   ├── contracts/         # 平台无关消息、生命周期与共享默认值契约
│   ├── core/              # Agent 核心：分类、澄清、规划、执行
│   ├── engine/            # 运行时引擎：主循环、命令调度
│   ├── feishu/            # 飞书集成
│   ├── infrastructure/    # 注册表、消息队列、日志、实例
│   ├── knowledge/         # 知识库管理
│   ├── mcp/               # MCP 桥接（可选）
│   ├── memory/            # 三层记忆
│   ├── resources/         # wheel 内置默认配置等运行时资源
│   ├── scheduled_tasks/   # 定时任务
│   ├── security/          # 沙箱
│   ├── session/           # 会话管理
│   ├── skills/            # 技能加载、ClawHub
│   ├── testing/           # Agent 测试适配器与验证运行器
│   ├── tools/             # 工具实现
│   ├── types/             # 类型定义
│   └── utils/             # 通用错误处理与会话 ID 工具
├── docs/                  # 文档
├── tests/                 # pytest 测试
├── scripts/               # 维护脚本
├── workspaces/            # 运行时状态（不入库）
├── pyproject.toml
└── README.md
```

## 开发与测试

本地门禁与 pytest 命令见 **[INDEX.md](docs/INDEX.md) §测试与质量** 与 **[ENGINEERING.md](docs/ENGINEERING.md) §2**（CI 矩阵、覆盖率目标、可选 perf smoke）。

**技术栈**：Python 3.10+ · OpenAI API · 飞书 SDK (lark-oapi) · pytest

## 专题文档

| 分类 | 文档 |
|------|------|
| 核心 | [USER_GUIDE.md](docs/USER_GUIDE.md) · [CHANGELOG.md](CHANGELOG.md) · [INDEX.md](docs/INDEX.md) |
| 用户与运维 | [CLI.md](docs/CLI.md) · [DEPLOYMENT.md](docs/DEPLOYMENT.md) · [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [FEISHU.md](docs/FEISHU.md) · [KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) |
| 架构与专题 | [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md) · [SECURITY.md](docs/SECURITY.md) · [SELF_OPT.md](docs/SELF_OPT.md) |
| 性能 | [PERFORMANCE.md](docs/PERFORMANCE.md) |
| 开发者 | [CONTRIBUTING.md](docs/CONTRIBUTING.md) · [PROMPT_GUIDELINES.md](docs/PROMPT_GUIDELINES.md) |
| 维护者 | [ENGINEERING.md](docs/ENGINEERING.md) · [TEST_COVERAGE_MATRIX.md](docs/TEST_COVERAGE_MATRIX.md) |

完整索引与 SSOT 对照见 **[INDEX.md](docs/INDEX.md)**。

## License

MIT
