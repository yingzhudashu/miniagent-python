# Mini Agent Python — 日常使用指南

> 安装、配置、首次启动见 **[README.md](../README.md)**。本文从日常使用起，面向已能跑通 Agent 的用户。  
> Mini Agent Python | 版本: 2.2.0 | 最后更新: 2026-07-14 | 与 `miniagent.__version__` 对齐 | 未发版行为见 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`

### 章节迁移对照（原 USER_GUIDE 编号）

| 原 § | 新位置 |
|------|--------|
| §1–§6（前言、安装、配置、启动） | [README.md](../README.md) |
| 原 §7 | 本文 §1 日常对话 |
| 原 §8 | 本文 §2 点命令速查 |
| 原 §9–§20 | 本文 §3–§14 |

---

## 目录

1. [日常对话怎么用](#1-日常对话怎么用)
2. [点命令（`/`）速查](#2-点命令速查)
3. [定时任务（/schedule）](#3-定时任务)
4. [会话与多会话](#4-会话与多会话)
5. [飞书（可选）](#5-飞书可选)
6. [联网搜索与浏览器工具（可选）](#6-联网搜索与浏览器工具可选)
7. [技能与 ClawHub（可选）](#7-技能与-clawhub可选)
8. [知识库（/kb）](#8-知识库kb)
9. [MCP 工具（可选）](#9-mcp-工具可选)
10. [状态目录、备份与 Git](#10-状态目录备份与-git)
11. [常见问题（FAQ）](#11-常见问题faq)
12. [安全与隐私清单](#12-安全与隐私清单)
13. [进阶阅读与开发](#13-进阶阅读与开发)
14. [文档索引](#14-文档索引)

---

## 1. 日常对话怎么用

1. 启动后，在提示处 **直接输入中文或英文需求** 即可。  
2. 若任务需要工具，界面可能出现 **思考过程** 或 **工具调用提示**（取决于通道与配置），等待结束即可。  
3. 若模型建议的规划过长，你仍可用点命令（第 2 章）查看状态、切换会话等。  
4. **规划 / 执行** 对用户而言不必深究：可理解为「先想清楚步骤，再逐步做完」。

---

## 2. 点命令（`/`）速查

多数以下命令在 **CLI 与飞书** 中均可使用（前缀为斜杠 `/`）。**`/schedule` 的 add/update/remove/enable/disable** 仅允许在本机 CLI 执行（见第 3 章）；部分 **`/session` 变异** 亦仅允许在本机 CLI（见第 4 章）。**完整说明、示例输出与边界情况** 见 [CLI.md](CLI.md)。

### 2.1 最常用命令（速查）

| 命令 | 作用 |
|------|------|
| `/help` | 显示帮助 |
| `/status` | 查看运行状态（含通道绑定与 CLI 聚焦模式，见 [FEISHU.md §通道绑定](FEISHU.md#通道绑定)；不中断当前执行） |
| `/session list` / `/session switch <id>` | 列出 / 切换会话（切换会同步 CLI 与自动私聊绑定） |
| `/feishu start` / `/feishu stop` / `/feishu status` | 飞书 WebSocket 长连接控制 |
| `/schedule list` | 查看定时任务（增删改见第 3 章，须在本地 CLI） |
| `/reload-config` | 重新加载配置文件（热更新） |
| `/config [section]` | 查看配置概览；指定 section 时查看该部分 |
| `/model [name]` | 显示当前模型；指定 name 时切换模型 |
| `/doctor` | 诊断安装与配置 |

**完整命令表、示例输出与边界情况** → [CLI.md](CLI.md)。

### 2.2 使用提示

- 命令前必须是 **`/`**（斜杠），后面跟子命令与参数，中间空格按 [CLI.md](CLI.md) 示例。
- 不确定时先 `/help` 或 `/status`。
- **模糊匹配**：输入错别字时系统会提示"您是否想输入 xxx？"，如 `/sttatus` → 提示 `/status`。
- **Tab 补全**：输入 `/` 命令或 `@file:` 文件路径时按 `Tab` 键自动补全。

---

## 3. 定时任务

在本地 CLI 用 **`/schedule`** 管理持久化任务；到达时间后请求经消息队列进入与手动输入相同的 Agent 路径。任务文件：**`{paths.state_dir}/scheduled_tasks/tasks.json`**（canonical 路径见 [ENGINEERING.md §3](ENGINEERING.md#3-状态目录与测试隔离)）。

- **语法与示例**：[CLI.md §/schedule](CLI.md)
- **飞书限制**：默认仅 `list` / `show`；`add` / `remove` 等须在本地 CLI
- **架构与配置**：[ARCHITECTURE.md「定时任务子系统」](ARCHITECTURE.md#定时任务子系统)、`miniagent/resources/config.defaults.json` 的 `scheduled_tasks` 节

---

## 4. 会话与多会话

- **会话**就像「不同的聊天窗口」，历史与部分配置相互隔离。  
- 使用 `/session list` 查看列表；在 **本地 CLI** 用 `/session switch` 切换到工作上下文。  
- **飞书里**（默认）发送 `/session switch` / `create` / `rename` 等变异子命令**不会**修改与 CLI 共享的 `active_session_id` 或会话存储，仅返回提示；请在本地终端执行，或设置 **`feishu.dot_commands_full=true`**（见 [FEISHU.md](FEISHU.md)、[CLI.md](CLI.md)）。  
- 会话与记忆落盘位置由 **`paths.state_dir`** 控制，详见第 10 章与 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。

---

## 5. 飞书（可选）

1. 安装依赖：`pip install -e ".[feishu]"`。  
2. 在飞书开放平台创建企业自建应用；App ID、App Secret、事件订阅与权限见 **[FEISHU.md](FEISHU.md) §快速开始**（SSOT）。  
3. 将凭证填入 `config.user.json` 的 `secrets` 部分（勿泄露）。  
4. 启动 `python -m miniagent --feishu` 或在 CLI 中 `/feishu start`。  

**通道绑定**（CLI 与飞书私聊共享会话）、入站锁、内置工具、附件路径等运维细节见 [FEISHU.md](FEISHU.md)（含 [§通道绑定](FEISHU.md#通道绑定)）与 [SECURITY.md](SECURITY.md)。升级迁移见 [README.md §配置](../README.md#配置) 与 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`。

---

## 6. 联网搜索与浏览器工具（可选）

- **联网搜索（Tavily）**：在 `config.user.json` 的 `secrets` 部分配置 `tavily_api_key` 或 `web_search_api_key`。未配置时，若模型尝试调用搜索工具，会得到 **明确错误提示**，不影响其它工具。  
- **Stack Overflow / Stack Exchange 排障检索**：`builtin-stackexchange` 使用公开 API，匿名模式即可工作；`secrets.stack_exchange_key` 仅用于提高配额。Agent 只在报错、异常、兼容性、性能、安装构建、驱动、网络和硬件排障时主动查询，并按问题选择 Stack Overflow、Super User、Server Fault、Ask Ubuntu、Unix & Linux、Electrical Engineering 等站点。查询会脱敏常见凭据、邮箱、私有主机和本地路径；社区票数与采纳状态只是经验信号，最终建议仍需核对本地环境和当前官方资料。引用社区答案时保留作者与原帖链接。
- **浏览器正文抽取**：需 `[browser]` 与 Playwright 浏览器安装；用于部分需渲染的网页。  

超时等见 `miniagent/resources/config.defaults.json` 的 `agent` 节（如 `agent.tool_timeout`）。

---

## 7. 技能与 ClawHub（可选）

- 默认技能根目录为仓库下 **`workspaces/skills/`**（可在 `config.user.json` 设置 `paths.skills_dir`）。  
- **内置基线**：启动时会补齐 `skill-creator`、`skill-vetter`、`builtin-web` 和 `builtin-stackexchange`；新增的 Stack Exchange 技能不会覆盖用户修改过的 `builtin-web`。`skill-creator` 来自 [anthropics/skills](https://github.com/anthropics/skills) 并包含 `LICENSE.txt`。
- **从 PyPI 安装 wheel** 时也包含上述基线模板；启动会恢复缺失目录。已有用户定制会保留，托管的 `builtin-web` 历史版本文件仅在内容仍与已知官方版本完全一致时升级。
- **扩展**：可从 ClawHub 安装更多技能包，引导脚本见 `scripts/bootstrap_clawhub_skills.py`（参数以官方技能页为准；脚本仅为额外安装，不替代内置基线）。  
- 第三方许可清单与合规说明见 **[workspaces/skills/THIRD_PARTY_SKILLS.md](../workspaces/skills/THIRD_PARTY_SKILLS.md)**（SSOT）。

---

## 8. 知识库（/kb）

将本地文档挂载入 Agent，对话时自动检索相关内容拼入上下文。示例：`/kb mount ./my-docs 手册` → `/kb search 部署流程 手册`。

完整目录结构、Agent 工具与全部子命令见 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)；命令示例见 [CLI.md](CLI.md) `/kb` 节。

---

## 9. MCP 工具（可选）

1. `pip install -e ".[mcp]"`。  
2. 在 `config.user.json` 的 `mcp.stdio_command` 中设置 **JSON 数组** 形式的启动命令，例如 `["npx","-y","@组织/包名"]`（请替换为你信任的 MCP 服务）。  
3. 可选：在 `mcp.stdio_env` 中设置传给 MCP 子进程的环境变量，例如 `{"API_KEY": "..."}`。  
4. 重启进程后，工具以 `mcp_*` 名称注册，并自动加入 **`mcp` 工具箱** 供规划器选用。  

**工具可见性**（与 `agent.tool_selection_strategy` 相关）：

| 策略 | MCP 工具何时对 LLM 可见 |
|------|-------------------------|
| `toolbox`（默认） | 规划器在 `required_toolboxes` 中包含 `mcp`，或计划为空工具箱列表时 |
| `auto` | 同上；无工具箱时仅核心工具（不含 MCP） |
| `all` | 始终可见 |

具体配置见 `miniagent/resources/config.defaults.json` 的 `mcp` 节与 [ENGINEERING.md](ENGINEERING.md) §1。

---

## 10. 状态目录、备份与 Git

### 10.1 默认布局

默认布局下，**项目业务状态**（会话、锁、飞书去重、记忆索引等）写入 miniagent 安装/源码根下的 **`workspaces/projects/{project_key}/`**（按启动 cwd 自动区分）；**实例注册表** 位于 `workspaces/instances/`。可在 `config.user.json` 设置绝对 `paths.state_dir` 或通过 `MINIAGENT_PATHS_STATE_DIR` 将项目数据放到其它磁盘路径，便于备份或多副本隔离。

### 10.2 哪些不应提交到 Git

根目录 `.gitignore` 已忽略多数运行时目录与文件（如 `workspaces/sessions/`（即 `{paths.state_dir}/sessions/`，见 [ENGINEERING.md](ENGINEERING.md) §3）、`workspaces/scheduled_tasks/`、`workspaces/memory/`、`workspaces/feishu/`、`keyword-index.json` 等）。**不要** 强行把含隐私对话或密钥的文件 `git add` 进去。政策说明见 [ENGINEERING.md](ENGINEERING.md) §3.1。

### 10.3 备份建议

若 `paths.state_dir` 指向重要数据目录，请用你自己的备份方案（加密盘、权限控制、定期拷贝）。详见 [DEPLOYMENT.md](DEPLOYMENT.md) 与 [SECURITY.md](SECURITY.md)。

---

## 11. 常见问题（FAQ）

最高频现象速查；**完整现象→章节映射**见 [TROUBLESHOOTING.md §常见现象速查](TROUBLESHOOTING.md#常见现象速查)。

| 现象 | 建议 |
|------|------|
| 启动报错 / API 密钥 | [TROUBLESHOOTING §启动问题](TROUBLESHOOTING.md#启动问题) |
| 飞书无响应 | [FEISHU.md](FEISHU.md) · [TROUBLESHOOTING §飞书](TROUBLESHOOTING.md#飞书集成问题) |
| 卡住 / 响应慢 | `/status`；[TROUBLESHOOTING §运行问题](TROUBLESHOOTING.md#运行问题) |
| 内存 / 会话过多 | [PERFORMANCE Part B](PERFORMANCE.md#part-b--运行时调优) · `/session list` |

---

## 12. 安全与隐私清单

1. **`config.user.json`** 仅本机保存，权限收紧；勿提交 Git。  
2. **不要在截图、录屏、聊天里** 暴露完整密钥或企业内部令牌。  
3. **共享电脑**：使用独立用户目录与独立 `paths.state_dir`，用完可删除状态目录。  
4. **工具能力**：文件与命令受沙箱等约束，见 [SECURITY.md](SECURITY.md)；不要给不可信人员开放你的运行环境。  
5. **备份介质**：会话与记忆可能含敏感业务文本，备份同样需加密与访问控制。
6. **外部检索**：排障查询会发送到 Stack Exchange API；即使有自动脱敏，也不要把内部源码、客户数据或完整私有日志作为搜索词。

---

## 13. 进阶阅读与开发

- 参与开发与代码规范：[CONTRIBUTING.md](CONTRIBUTING.md)  
- 仓库卫生、CI、单一事实来源：[ENGINEERING.md](ENGINEERING.md)  
- 架构与数据流：[ARCHITECTURE.md](ARCHITECTURE.md)  
- 部署与运维：[DEPLOYMENT.md](DEPLOYMENT.md)  
- 自我优化（提案与 Trace 分析）：[SELF_OPT.md](SELF_OPT.md)

普通用户日常使用 **读到第 11 章即可**；开发贡献请读 [CONTRIBUTING.md](CONTRIBUTING.md) / [ENGINEERING.md](ENGINEERING.md)，并见 **第 14 章** [文档索引](#user-guide-sec14-advanced)。

---

## 14. 文档索引

**完整专题列表、SSOT 对照**以 [INDEX.md](INDEX.md) 为准。用户入门以 [README.md](../README.md) 为准。

<a id="user-guide-sec14-advanced"></a>

贡献者与维护者路径（架构、工程、性能、输出格式、提示词规范等）见 [INDEX.md](INDEX.md)「文档分类」与 [README.md §按角色阅读](../README.md#按角色阅读)。

---

**结语**：完成 [README](../README.md) 安装与启动后，建议先熟悉 **自然语言提问** 与 **`/help` / `/status` / `/session list`**，再按需打开飞书、知识库、搜索与技能。遇到问题优先查第 11 章 FAQ 与 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
