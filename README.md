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

# 联网检索（天气、新闻等）：在 .env 中配置 TAVILY_API_KEY（或 WEB_SEARCH_API_KEY）
# 可选：无头浏览器抓取 CSR 页面 — pip install -e ".[browser]" && playwright install chromium
# 可选：MCP stdio 工具 — pip install -e ".[mcp]"，并在 .env 配置 MINIAGENT_MCP_STDIO（见 .env.example）
# 可选：ClawHub 基线技能 — python scripts/bootstrap_clawhub_skills.py（slug 以 https://clawhub.ai 技能页为准，若默认失败可用 --slug author/slug）

# 可选：将状态目录迁出仓库（测试 / 多副本部署）
# PowerShell: $env:MINI_AGENT_STATE = "$env:TEMP\miniagent-state"
# bash: export MINI_AGENT_STATE=/tmp/miniagent-state

# 启动
python -m miniagent                  # CLI 模式
python -m miniagent --feishu         # CLI + 飞书
python -m miniagent --stop           # 列出实例；交互停止 / --stop --all / --stop <id>...
```

新进程注册时会自动删除磁盘上 **PID 已退出** 的旧实例注册目录，**不会**终止仍在运行的其它 Agent。详见 [docs/INSTANCE_REGISTRY.md](docs/INSTANCE_REGISTRY.md)。

### 联网工具与配置说明

- **`web_search`**（Tavily）需配置密钥；未配置时调用会返回明确错误，不影响其余工具。
- **`browser_extract_text`** 依赖可选依赖 `miniagent-python[browser]`；未安装时返回安装提示。
- **内置工具始终出现在工具列表中**；不需要联网时请勿配置 Tavily Key 即可。可选外部 JSON（`MINIAGENT_CONFIG`）中的 `tools.web.search` **当前不会**单独开关注册项（与部分外部产品配置字段仅为语义对齐）；若需避免模型调用搜索，可在系统提示或策略侧约束。
- **自我优化工具**（`self_inspect`、`generate_proposal` 等）默认注册；生产环境若需收敛暴露面，可设置环境变量 **`MINIAGENT_SELF_OPT_TOOLS=0`**。详见 [.env.example](.env.example)。

### 技能目录迁移

旧版本若将技能装在仓库根目录 **`skills/`**，请迁移至 **`workspaces/skills/`**（或设置 **`MINI_AGENT_SKILLS`** 指向原目录），否则引擎不会加载旧路径。

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

源码包为 `miniagent/`；子包包括 `runtime`（组合根）、`core`（规划/执行）、`engine`（主循环与命令）、`feishu`、`infrastructure`、`memory`、`session`、`skills`、`tools`、`mcp`（可选 stdio MCP）、`security`、`types`。

**与仓库一致的完整目录树**见 [docs/INDEX.md](docs/INDEX.md) §项目结构。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | **零基础使用指南**（安装、配置、点命令、飞书/可选能力、FAQ、安全） |
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
python -m pytest tests/ -v       # 以 pytest 收集为准
python -m ruff check miniagent tests
python -m compileall -q miniagent
```

## 技术栈

- Python 3.10+
- OpenAI API (GPT-4o-mini)
- 飞书 SDK (lark-oapi, WebSocket)
- pytest (单元测试)

**可选 pip extra**（与 [`pyproject.toml`](pyproject.toml) 一致；权威说明见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §1）：`dev`（pytest / ruff）、`feishu`（lark-oapi）、`browser`（playwright）、`mcp`（官方 mcp SDK）。

## License

MIT
