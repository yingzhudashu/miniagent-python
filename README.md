# Mini Agent Python

基于 LLM 的两阶段智能代理系统。支持 CLI 和飞书双通道接入。

## 特性

- **两阶段架构**: Plan（规划）→ Execute（执行），精确控制工具调用
- **ReAct 循环**: Think → Act → Observe，多轮推理直到任务完成
- **三层记忆**: 短期记忆 / 活动日志 / 语义检索
- **双通道接入**: 同一进程内 CLI 主循环 + 可选飞书 WebSocket 长轮询（无单独「纯飞书」入口）；飞书可选内置工具（发文件、云盘、云文档等）见 [docs/FEISHU.md](docs/FEISHU.md)，需 `pip install -e ".[feishu]"` 与 `.env` 中 `MINIAGENT_FEISHU_TOOLS=1`，或（未显式关闭该变量时）`MINIAGENT_FEISHU_TOOLS_AUTO=1` 且已配置 `FEISHU_APP_ID`/`SECRET`；创建文档的可选分享链接前缀见 `FEISHU_DOCX_URL_PREFIX`（详表见 FEISHU.md）
- **消息队列**: queue（按序）/ preemptive（打断）双模式
- **定时任务**: 持久化任务表 + 进程内调度，经与聊天相同的消息队列执行 Agent 回合；CLI 下可用 `run_dot_command`（`.schedule …`）或 `manage_scheduled_task` 结构化接口
- **多实例**: 注册表 + 心跳，支持多终端并行
- **可插拔技能**: 动态加载，ClawHub 技能市场
- **自我优化**: 代码检查 + 优化提案 + Git 快照
- **沙箱安全**: 路径白名单 + 循环检测 + 权限控制

**会话文件与管线**：`UnifiedEngine.run_agent_with_thinking` 会把当前会话的 `files` 目录注入执行阶段；若直接调用 `run_pipeline`，默认 `ToolContext` 仍为 `MINI_AGENT_WORKSPACE` / 进程 cwd，不会自动与会话 `files` 对齐，需要自行传入 `ToolContext`。

**执行轮数**：`AGENT_MAX_TURNS` 默认 **400**；规划器建议的轮数不会把该上限压小。分步模式下单步上限见 `MINIAGENT_STEP_MAX_TURNS`（默认 **48**，见 `docs/ARCHITECTURE.md`）。

**终端 Markdown 渲染**：全屏 CLI 下 **Assistant 最终回复** 在已安装可选依赖 `pip install -e ".[cli]"`（Rich）时，会将 Markdown（含常见 GFM 表格）渲染为彩色 ANSI；未安装则回退为原始文本。设置 **`MINIAGENT_CLI_RAW_MARKDOWN=1`** 可强制关闭回复区 Rich。思考过程在设置 **`MINIAGENT_CLI_THINKING_RICH=1`** 且 transcript sink 支持 ANSI 块时，仅对**非流式**思考片段尝试 Rich：**流式**规划/执行正文仍为纯文本；默认开启的同轮 **merge_tools** 工具行仍为纯文本。全屏 TUI 下思考 Rich 宽度与回复区对齐。Rich 思考块在 transcript 中为裸 ANSI，与周边 `cli-think-body` 样式可能略有差异。未安装 Rich 时启动可打印安装提示，**`MINIAGENT_WELCOME_CLI_HINT=0`** 可关闭。

## 快速开始

```bash
# 安装（<repo-url> 为占位符，请换为实际远程；fork 说明见 docs/CONTRIBUTING.md）
git clone <repo-url>
cd miniagent-python
pip install -e ".[dev,typing]"       # 开发：与默认 CI `test` job 一致（pytest / ruff / pytest-cov / mypy 试点）
# pip install -e ".[dev]"           # 若不需要本地跑 mypy，可仅用 dev extra
# pip install -e ".[dev,feishu]"    # 若需本地跑通飞书 SDK 相关路径
# pip install -e ".[cli]"           # 终端内将 Assistant 的 Markdown 渲染为彩色样式（Rich）
# 仅需运行时：pip install -e .
cp .env.example .env       # 编辑填入 OPENAI_API_KEY

# 联网检索（天气、新闻等）：在 .env 中配置 TAVILY_API_KEY（或 WEB_SEARCH_API_KEY）
# 可选：无头浏览器抓取 CSR 页面 — pip install -e ".[browser]" && playwright install chromium
# 可选：MCP stdio 工具 — pip install -e ".[mcp]"，并在 .env 配置 MINIAGENT_MCP_STDIO（见 .env.example）
# 内置基线技能：workspaces/skills/skill-creator（Apache-2.0，自 anthropics/skills）、skill-vetter（审查说明）；可选从 ClawHub 安装更多 — python scripts/bootstrap_clawhub_skills.py（`author/slug` 会装到 slug 最后一段目录；详情无 files 时会试 /download，仍失败则见 THIRD_PARTY_SKILLS.md）

# 可选：将状态目录迁出仓库（测试 / 多副本部署）
# PowerShell: $env:MINI_AGENT_STATE = "$env:TEMP\miniagent-state"
# bash: export MINI_AGENT_STATE=/tmp/miniagent-state

# 启动
python -m miniagent                  # CLI 模式
python -m miniagent --feishu         # CLI + 飞书
python -m miniagent --stop           # 列出实例；交互停止 / --stop --all / --stop <id>...
```

与 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) 开发环境节相比：README 此处**默认**使用 `pip install -e ".[dev,typing]"`，以便与本节「测试」中的 `mypy` 及 CI `test` job 一致；CONTRIBUTING 仍以 `.[dev]` 为默认并在注释中说明如何改为 `.[dev,typing]`。若以合并前完整门禁为准，两处最终应统一到 [ENGINEERING.md](docs/ENGINEERING.md) §2 的命令块。

### 定时任务

- 任务保存在 `MINI_AGENT_STATE/scheduled_tasks/tasks.json`（未设置 `MINI_AGENT_STATE` 时默认为仓库下 `workspaces/`）。
- 终端：`.schedule list` 查看用法；`add` 子命令须用 ` -- `（空格、两个连字符、空格）把前面的参数和后面的 prompt 分开。
- Agent（本地 CLI 会话）：内置 **`run_dot_command`** 可执行与终端一致的 `.schedule …`；内置 **`manage_scheduled_task`** 用 JSON 增删改查，减少拼写错误。
- 飞书侧：与 `.session` 类似，**仅允许** `.schedule list` / `show`，`add/remove/enable/disable` 须在本地 CLI 执行。
- 环境变量：`MINIAGENT_DISABLE_SCHEDULED_TASKS=1` 关闭后台调度循环；`MINIAGENT_SCHEDULE_TOOLS=0` 不注册 `manage_scheduled_task`；`MINIAGENT_CLI_DOT_TOOLS=0` 不注册 `run_dot_command`。

新进程注册时会自动删除磁盘上 **PID 已退出** 的旧实例注册目录，**不会**终止仍在运行的其它 Agent。详见 [docs/INSTANCE_REGISTRY.md](docs/INSTANCE_REGISTRY.md)。

### 联网工具与配置说明

- **`web_search`**（Tavily）需配置密钥；未配置时调用会返回明确错误，不影响其余工具。
- **`browser_extract_text`** 依赖可选依赖 `miniagent-python[browser]`；未安装时返回安装提示。
- **内置工具始终出现在工具列表中**；不需要联网时请勿配置 Tavily Key 即可。可选外部 JSON（`MINIAGENT_CONFIG`）中的 `tools.web.search` **当前不会**单独开关注册项（与部分外部产品配置字段仅为语义对齐）；若需避免模型调用搜索，可在系统提示或策略侧约束。
- **自我优化工具**（`self_inspect`、`generate_proposal` 等）默认注册；生产环境若需收敛暴露面，可设置环境变量 **`MINIAGENT_SELF_OPT_TOOLS=0`**。详见 [.env.example](.env.example)。
- **点命令工具**：Agent 执行阶段可通过内置工具 **`run_dot_command`** 调用与终端一致的 `.help`、`.status`、`.session list` 等（由进程内 `CliLoopState` 注入；飞书侧与会话相关的变异命令仍受限制）。若不需要该能力，可设置 **`MINIAGENT_CLI_DOT_TOOLS=0`** 关闭注册。详见 [.env.example](.env.example) 与 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

### 技能目录迁移

仓库 **`workspaces/skills/skill-creator`** 与 **`skill-vetter`** 为内置基线（**克隆源码仓库或 `pip install -e .` 后随目录存在**）。发行版 **wheel** 默认不把 `workspaces/skills` 打进安装包：若仅用 **`pip install miniagent-python`** 且当前工作目录下没有完整仓库树，默认技能根路径下可能没有预置包；需要基线时请 **克隆仓库**、**editable 安装**，或将仓库中的 **`workspaces/skills/skill-creator`** 与 **`skill-vetter`** 拷贝到你的 `MINI_AGENT_SKILLS`（或默认 `workspaces/skills`）目录。第三方与同步说明见 [workspaces/skills/THIRD_PARTY_SKILLS.md](workspaces/skills/THIRD_PARTY_SKILLS.md)。

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
| [docs/EVALUATION_LOCAL.md](docs/EVALUATION_LOCAL.md) | 可选：本地离线测评与产物约定 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 性能 KPI、合成冒烟、基线与剖析 |
| [docs/CHANNEL_BINDING.md](docs/CHANNEL_BINDING.md) | 通道绑定 |
| [docs/CYBERNETICS_PLAN.md](docs/CYBERNETICS_PLAN.md) | 控制论/自适应路线（规划稿） |

**完整专题列表与目录树**以 [docs/INDEX.md](docs/INDEX.md) 为准。

## 测试

```bash
python -m pytest tests/ -q -m "not evaluation"   # 与默认 CI 一致（排除 tests/evaluation 下 marker）
python -m pytest tests/ -q                       # 含评测子目录全部用例
python -m ruff check miniagent tests
python -m compileall -q miniagent
python -m mypy miniagent/types                   # 与默认 CI `test` job 一致（需 pip install -e ".[dev,typing]"）
```

用例数量以 `pytest tests/ --collect-only -q` 的收集结果为准（勿在文档中硬编码条数以免漂移）；与 [docs/ENGINEERING.md](docs/ENGINEERING.md) §5 核对清单一致。

评测目录说明与产物勿提交约定见 [docs/EVALUATION_LOCAL.md](docs/EVALUATION_LOCAL.md)；手动触发仅跑评测见 [docs/ENGINEERING.md](docs/ENGINEERING.md) 第 2 节。

## 技术栈

- Python 3.10+
- OpenAI API (GPT-4o-mini)
- 飞书 SDK (lark-oapi, WebSocket)
- pytest (单元测试)

**可选 pip extra**（与 [`pyproject.toml`](pyproject.toml) 一致；权威说明见 [docs/ENGINEERING.md](docs/ENGINEERING.md) 第 1 节）：`dev`（pytest / ruff / pytest-cov）、`typing`（mypy）、`cli`（Rich）、`feishu`（lark-oapi）、`browser`（playwright）、`mcp`（官方 mcp SDK）。

## License

MIT
