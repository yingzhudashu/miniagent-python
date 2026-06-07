# Mini Agent Python

![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-2.1.0-blue)
![Tests](https://img.shields.io/badge/tests-1408-green)
![Coverage](https://img.shields.io/badge/coverage-85%25-yellow)

基于 LLM 的多阶段智能代理系统。支持 CLI 和飞书双通道接入。

## 特性

- **多阶段架构**: Phase 0 (分类) → Phase 0.5 (需求澄清) → Phase 1 (规划) → Phase 2 (执行)，精确控制工具调用
- **三步需求澄清**: Wittgenstein（语言边界）→ Socrates（反向追问）→ Polanyi（示例传递）
- **ReAct 循环**: Think → Act → Observe，多轮推理直到任务完成
- **三层记忆**: 短期记忆 / 活动日志 / 语义检索
- **双通道接入**: 同一进程内 CLI 主循环 + 可选飞书 WebSocket 长连接（无单独「纯飞书」入口）；出站默认 `feishu.reply_target=reply`；内置飞书工具默认由 `feishu.tools_auto` 注册。详见 [docs/FEISHU.md](docs/FEISHU.md)
- **消息队列**: queue（按序）/ preemptive（打断）双模式
- **定时任务**: 持久化任务表 + 进程内调度，经与聊天相同的消息队列执行 Agent 回合；CLI 下可用 `run_dot_command`（`.schedule …`）或 `manage_scheduled_task` 结构化接口
- **多实例**: 注册表 + PID 存活清理（心跳仅观测），支持多终端并行；详见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §3.3
- **可插拔技能**: 动态加载，ClawHub 技能市场
- **自我优化**: 代码检查 + 优化提案 + Git 快照
- **沙箱安全**: 路径白名单 + 循环检测 + 权限控制

**会话文件与管线**：`UnifiedEngine.run_agent_with_thinking` 会把当前会话的 `files` 目录注入执行阶段；若直接调用 `run_pipeline`，默认 `ToolContext` 仍为 `MINIAGENT_PATHS_WORKSPACE` / 进程 cwd，不会自动与会话 `files` 对齐，需要自行传入 `ToolContext`。

**执行轮数**：`agent.max_turns` 默认 **400**；分步子循环上限为内置常量（默认 **48**，见 `docs/ARCHITECTURE.md`）。

**终端 Markdown 渲染**：全屏 CLI 下安装 `pip install -e ".[cli]"`（Rich）可将 Assistant 回复渲染为彩色 ANSI；`cli.welcome_hint` 控制欢迎安装提示。

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
cp config.defaults.json config.user.json  # 编辑填入 secrets.openai_api_key

# 联网检索（天气、新闻等）：在 config.user.json 的 secrets 部分配置 tavily_api_key
# 可选：无头浏览器抓取 CSR 页面 — pip install -e ".[browser]" && playwright install chromium
# 可选：MCP stdio 工具 — pip install -e ".[mcp]"，并在 config.user.json 配置 mcp.stdio_command（见 config.defaults.json）
# 内置基线技能：workspaces/skills/skill-creator（Apache-2.0，自 anthropics/skills）、skill-vetter（审查说明）；可选从 ClawHub 安装更多 — python scripts/bootstrap_clawhub_skills.py（`author/slug` 会装到 slug 最后一段目录；详情无 files 时会试 /download，仍失败则见 THIRD_PARTY_SKILLS.md）

# 可选：在 config.user.json 设置 paths.state_dir 将状态目录迁出仓库

# 启动
python -m miniagent                  # CLI 模式
python -m miniagent --continue     # 继续上次 CLI 会话
python -m miniagent --session <ID> # 启动并绑定到指定会话
python -m miniagent --feishu         # CLI + 飞书
python -m miniagent --feishu --continue  # CLI + 飞书，并继续上次会话
python -m miniagent --stop           # 列出实例；交互停止 / --stop --all / --stop <id>...
```

README、[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) 与 [docs/ENGINEERING.md](docs/ENGINEERING.md) §2 均以 `pip install -e ".[dev,typing]"` 与默认 CI `test` job 一致；完整本地门禁命令以 ENGINEERING §2 为准。

### 定时任务

- 任务表：`{paths.state_dir}/scheduled_tasks/tasks.json`（默认 `workspaces/scheduled_tasks/`）。
- 本机 CLI 用 **`.schedule`** 管理（五段 cron / every / once）；飞书侧默认仅 **list** / **show**。
- `primary` 任务在飞书私聊已绑定时可镜像推送（`scheduled_tasks.feishu_mirror=false` 可关）。

操作细节见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md) §8、[docs/CLI.md](docs/CLI.md)。

新进程注册时会自动删除磁盘上 **PID 已退出** 的旧实例注册目录，**不会**终止仍在运行的其它 Agent。详见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §3.3。

### 联网、点命令与技能（要点）

- **联网**：`web_search` 需 `secrets.tavily_api_key`；`browser_extract_text` 需 `[browser]`。见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md) §11。
- **自我优化 / 点命令工具**：默认注册；`cli.dot_tools_enabled=false` 可关闭点命令。飞书侧限制见 [docs/CLI.md](docs/CLI.md)、[docs/FEISHU.md](docs/FEISHU.md)。
- **技能目录**：内置 `workspaces/skills/skill-creator`、`skill-vetter`；wheel 不含技能包时需克隆或 editable 安装。迁移与 ClawHub 见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md) §12。

## 常用命令

| 命令 | 说明 |
|------|------|
| `/status` | 检查 Agent 状态（不中断执行） |
| `/session list` | 列出所有会话 |
| `/session switch <id>` | 切换会话 |
| `/instance list` | 列出运行实例 |
| `/feishu start/stop` | 飞书控制 |
| `/queue status` | 消息队列状态 |
| `/help` | 显示完整帮助 |
| `/btw start <prompt>` | 启动后台任务（并行执行） |
| `/config [section]` | 查看配置概览；指定 section 时查看该部分 |
| `/model [name]` | 显示当前模型；指定 name 时切换模型 |

> 多数 `/` 命令在 CLI 与飞书均可使用；`/schedule` 的 add/update/remove/enable/disable 及部分 `/session` 变异仅允许在本机 CLI 执行（见 [docs/CLI.md](docs/CLI.md)）。

### 后台任务系统（并行执行）

使用 `/btw` 命令启动后台任务，不污染主对话历史：

```
/btw start 分析这个文件        # 启动后台任务
/btw status                    # 查看任务列表
/btw result <task_id>          # 获取任务结果
Ctrl+T                         # 快捷键查看任务列表
```

## 项目结构

源码包为 `miniagent/`；子包包括 `runtime`（组合根）、`core`（规划/执行）、`engine`（主循环与命令）、`feishu`、`infrastructure`、`memory`、`session`、`skills`、`tools`、`mcp`（可选 stdio MCP）、`security`、`types`。

**与仓库一致的完整目录树**见 [docs/INDEX.md](docs/INDEX.md) §项目结构。

## 文档

**新手请先看** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)；下列为专题索引。

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
| [docs/SELF_OPT.md](docs/SELF_OPT.md) | 自我优化 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 性能 KPI、合成冒烟、基线与剖析 |
| [docs/CHANNEL_BINDING.md](docs/CHANNEL_BINDING.md) | 通道绑定 |
| `config.defaults.json` | 默认配置（含 User/Advanced 分层 `_config_guide`） |

**完整专题列表与目录树**以 [docs/INDEX.md](docs/INDEX.md) 为准。飞书卡片 JSON v2 调研见 [docs/FEISHU.md](docs/FEISHU.md)「调研与路线图」。

## 测试

```bash
python -m pytest tests/ -q -m "not evaluation"   # 与默认 CI 一致（排除 tests/evaluation 下 marker）
python -m pytest tests/ -q                       # 含评测子目录全部用例
python -m ruff check miniagent tests
python -m compileall -q miniagent
python -m mypy miniagent/types                   # 与默认 CI `test` job 一致（需 pip install -e ".[dev,typing]"）
```

用例数量以 `pytest tests/ --collect-only -q` 的收集结果为准（勿在文档中硬编码条数以免漂移）；与 [docs/ENGINEERING.md](docs/ENGINEERING.md) §5 核对清单一致。

评测目录说明与产物勿提交约定见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §5。

## 技术栈

- Python 3.10+
- OpenAI API (GPT-4o-mini)
- 飞书 SDK (lark-oapi, WebSocket)
- pytest (单元测试)

**可选 pip extra**（与 [`pyproject.toml`](pyproject.toml) 一致；权威说明见 [docs/ENGINEERING.md](docs/ENGINEERING.md) 第 1 节）：`dev`（pytest / ruff / pytest-cov）、`typing`（mypy）、`cli`（Rich）、`feishu`（lark-oapi）、`browser`（playwright）、`mcp`（官方 mcp SDK）。

## License

MIT
