# Mini Agent Python — 全项目使用指南（新手向）

> 本文面向 **零基础或仅有普通电脑使用经验** 的读者，按顺序操作即可完成安装、配置与日常使用。  
> 技术细节与命令全集以专题文档为准；本文提供 **路径导航 + 常见操作 + 安全习惯**。  
> 版本与仓库内 `miniagent.__version__` 对齐时请参见 [INDEX.md](INDEX.md) 页眉；**未发版行为**以 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` 为准。

---

## 目录

1. [前言：本项目能做什么](#1-前言本项目能做什么)
2. [开始前准备](#2-开始前准备)
3. [获取代码与安装](#3-获取代码与安装)
4. [首次配置（环境变量与 .env）](#4-首次配置环境变量与-env)
5. [第一次启动与退出](#5-第一次启动与退出)
6. [日常对话怎么用](#6-日常对话怎么用)
7. [点命令（`.`）速查](#7-点命令速查)
8. [定时任务（.schedule）](#8-定时任务)
9. [会话与多会话](#9-会话与多会话)
10. [飞书（可选）](#10-飞书可选)
11. [联网搜索与浏览器工具（可选）](#11-联网搜索与浏览器工具可选)
12. [技能与 ClawHub（可选）](#12-技能与-clawhub可选)
13. [MCP 工具（可选）](#13-mcp-工具可选)
14. [状态目录、备份与 Git](#14-状态目录备份与-git)
15. [常见问题（FAQ）](#15-常见问题faq)
16. [安全与隐私清单](#16-安全与隐私清单)
17. [进阶阅读与开发](#17-进阶阅读与开发)
18. [文档索引](#18-文档索引)（含 [进阶与维护](#user-guide-sec18-advanced) 专题链）

---

## 1. 前言：本项目能做什么

**Mini Agent Python** 是一个在本地（或你自己的服务器）上运行的 **智能助手程序**。它通过 **大语言模型（LLM）** 理解你的文字需求，并可在授权范围内 **调用工具**（例如读写工作区文件、执行命令、联网搜索等），尽量自动完成任务。

与「只能聊天」的网页机器人相比，典型差异包括：

| 能力 | 说明 |
|------|------|
| **两阶段** | 先 **规划** 再 **执行**（你可从回复节奏上感知：先有条理再动手），细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。 |
| **工具** | 模型可调用注册的工具（文件、命令、搜索等）；未配置的联网密钥不会偷偷联网。 |
| **CLI + 可选飞书** | 默认在终端里对话；也可 **同一进程** 里挂上飞书机器人（WebSocket 长连接），没有单独的「只飞书无终端」形态。 |
| **会话与记忆** | 多会话隔离，并支持跨会话记忆与检索；见 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。 |

**不适合的场景**：不要把它当作对公网匿名用户开放的多租户服务；部署与安全边界见 [SECURITY.md](SECURITY.md)、[DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 2. 开始前准备

### 2.1 你需要什么

- **电脑**：Windows、macOS 或 Linux 均可。
- **Python**：**3.10 或更高**（与仓库 `pyproject.toml` 中 `requires-python` 一致）。终端输入 `python --version` 或 `python3 --version` 可查看。
- **网络**：安装依赖、调用云端 LLM、以及（若启用）联网搜索时需要能访问对应服务商。
- **终端**：Windows 上可用 PowerShell 或「终端」应用；macOS/Linux 用自带终端即可。
- **Git（可选）**：从 Git 仓库克隆代码时需要；若只有 ZIP 源码包也可解压使用。

### 2.2 你需要准备什么账号或密钥（概念层面）

- **至少一种 LLM API**：程序通过 **OpenAI 兼容接口** 调用模型。你会有「API 地址」和「API 密钥」两个概念；密钥 **只应** 出现在本机 `.env` 或环境变量里，**不要** 写进聊天截图或发给陌生人。
- **可选**：飞书企业自建应用、Tavily 搜索密钥、Playwright 浏览器、MCP 服务等——用到再配置即可。

具体变量名见下一章与仓库根目录的 [.env.example](../.env.example)。

### 2.3 厂商控制台与注册细节

各服务商的 **注册、计费、控制台菜单** 以官方文档为准。本仓库侧的安装条件、端口与运行环境检查见 [DEPLOYMENT.md](DEPLOYMENT.md)；飞书侧步骤见 [FEISHU.md](FEISHU.md)。

---

## 3. 获取代码与安装

### 3.1 获取代码

若使用 Git（将 `<仓库地址>` 换成你的实际地址）：

```bash
git clone <仓库地址>
cd miniagent-python
```

若没有 Git，请用浏览器下载源码 ZIP 并解压，再在终端中 `cd` 到解压后的目录。

### 3.2 建议：使用虚拟环境

虚拟环境可以把本项目依赖与系统其它 Python 项目隔开，减少「装乱套」。

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

若 `python` 命令不存在，可尝试 `python3`。

### 3.3 安装本项目

**仅运行（最小）**

```bash
pip install -e .
```

**开发（含测试与静态检查，与默认 CI 一致）**

```bash
pip install -e ".[dev,typing]"
```

**可选能力（按需叠加）**

| 能力 | 安装命令 |
|------|----------|
| 飞书 SDK | `pip install -e ".[feishu]"` |
| 浏览器抓取（Playwright） | `pip install -e ".[browser]"` 然后按官方文档安装浏览器，如 `playwright install chromium` |
| MCP 官方 SDK | `pip install -e ".[mcp]"` |

可同时写：`pip install -e ".[dev,feishu]"` 等。权威列表见 [ENGINEERING.md](ENGINEERING.md) 与 [pyproject.toml](../pyproject.toml)。

### 3.4 启动方式说明

安装完成后，可用任一方式启动（等价入口）：

```bash
python -m miniagent
```

或（若 `PATH` 已包含脚本目录）：

```bash
miniagent
```

二者都对应 `pyproject.toml` 里的 `miniagent` 控制台脚本。

> **安装长文 SSOT**：与 [README.md](../README.md) 快速开始重复时，以本章为准；开发/CI 门禁安装见 [ENGINEERING.md](ENGINEERING.md) §2。

---

## 4. 首次配置（环境变量与 .env）

**升级迁移提示**（详见 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` Breaking）：飞书出站默认 `MINIAGENT_FEISHU_REPLY_TARGET=reply`；内置飞书工具默认由 `MINIAGENT_FEISHU_TOOLS_AUTO` 注册；已移除 `MINIAGENT_CONFIG` 外部 JSON，请用 `.env` 扁平变量；飞书请改用 `MINIAGENT_FEISHU_DOCX_URL_PREFIX`、`MINIAGENT_FEISHU_DOC_FOLDER_TOKEN`（旧名仍会读取并打弃用警告）。飞书细节见第 10 章。

### 4.1 创建 .env

在 **项目根目录**（与 `pyproject.toml` 同级）执行：

```bash
cp .env.example .env
```

Windows PowerShell 若无 `cp`，可用：

```powershell
Copy-Item .env.example .env
```

用任意文本编辑器打开 `.env`，按需取消注释并填写。**不要** 把填好密钥的 `.env` 上传到公开仓库；仓库已默认在 `.gitignore` 中忽略 `.env`。

### 4.2 必填（最小可运行）

至少需要能访问 LLM：

| 变量 | 含义 |
|------|------|
| `OPENAI_API_KEY` | 你的 API 密钥（由服务商控制台生成；**勿**在文档或截图中泄露）。 |
| `OPENAI_BASE_URL` | 可选。使用官方或兼容网关时按服务商说明填写。 |
| `OPENAI_MODEL` | 可选。默认可用服务商推荐的模型 id。 |

若暂时不理解 `BASE_URL`，可先只填密钥与模型，按服务商文档校对。

### 4.3 常用可选变量（摘选）

完整注释以 [.env.example](../.env.example) 为准。下表仅列新手常问的项：

| 变量 | 用途 |
|------|------|
| `MODEL_PROFILE` | 模型行为预设：`creative` / `balanced` / `precise` / `code` / `fast` 等。 |
| `AGENT_MAX_TURNS` | 单轮 ReAct 最大轮数，**默认 400**（见 `.env.example`）。结构化规划返回的 `maxTurns` **只会抬高不会压低**该上限。若开启分步执行（`MINIAGENT_PHASED_EXECUTION`）仍遇单步内用尽，需另调 **`MINIAGENT_STEP_MAX_TURNS`**（未设置时默认 **48**）。 |
| `MINIAGENT_TOOL_INTENT_MAX_CHARS` | 工具意图预览（如 `exec_command` 的命令片段）最大字符数，默认 4000；`0` 表示不截断。 |
| `AGENT_DEBUG` | `true` 时更啰嗦的日志；日常可 `false`。 |
| `TAVILY_API_KEY` 或 `WEB_SEARCH_API_KEY` | 启用联网搜索（Tavily）时使用其一即可。 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFICATION_TOKEN` | 飞书应用凭证；仅在使用飞书时填写。 |
| `MINI_AGENT_STATE` | 状态根目录，见第 14 章。 |
| `MINIAGENT_MCP_STDIO` | MCP stdio 启动命令的 JSON 数组字符串，见第 13 章。 |
| `MINIAGENT_CLI_RAW_MARKDOWN` | 设为 `1`/`true` 时关闭全屏 CLI 下 **Assistant 最终回复** 的 Rich Markdown 渲染（便于复制）。 |
| `MINIAGENT_CLI_THINKING_RICH` | 设为 `1`/`true` 且已 `pip install -e ".[cli]"` 时，对**非流式**思考片段尝试 Rich；**流式**规划/执行正文仍为纯文本；同轮 **merge_tools** 工具行仍为纯文本。 |
| `MINIAGENT_WELCOME_CLI_HINT` | 未安装 Rich 时启动是否打印「建议 pip install .[cli]」；默认 `1`，设为 `0`/`false` 关闭。 |
| `MINIAGENT_THINKING_SEGMENT_SEPARATOR` | 分步执行同一步内多段思考拼接符；留空为双换行，见 `.env.example`。 |
| `MINIAGENT_TOOL_FINISH_VERBOSE` | `1` 时 `history.json` 中工具块含参数与输出；默认 `0` 仅名称与成败。 |

### 4.4 从 OpenClaw JSON 迁移

若曾使用 OpenClaw 导出 JSON，请将字段写入 `.env`：`OPENAI_MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`AGENT_CONTEXT_WINDOW`、`AGENT_THINKING_DEFAULT`、`OPENAI_THINKING_BUDGET`、`OPENAI_MAX_TOKENS`（映射见 [.env.example](../.env.example) §2）。

---

## 5. 第一次启动与退出

### 5.1 仅终端（CLI）

```bash
python -m miniagent
```

看到欢迎信息后，可直接用 **自然语言** 输入需求。部分环境也可用 `quit` / `exit` 退出（与 [CLI.md](CLI.md) 一致）。

### 5.2 终端 + 飞书同时启动

需已安装 `[feishu]` 并配置飞书环境变量：

```bash
python -m miniagent --feishu
```

飞书 **不会** 单独占一个无终端的进程；始终与 CLI 主循环一起。更多见第 10 章与 [FEISHU.md](FEISHU.md)。

### 5.3 多实例与停止其它进程

```bash
python -m miniagent --stop
```

用于列出本机已注册实例并交互停止；`--stop --all` 或 `--stop 1 2` 等用法见 [README.md](../README.md) 与 [ENGINEERING.md](ENGINEERING.md) §3.3。  
说明：清理的是 **注册信息** 与 **你选择的进程**；不要随意结束他人机器上的进程。

---

## 6. 日常对话怎么用

1. 启动后，在提示处 **直接输入中文或英文需求** 即可。  
2. 若任务需要工具，界面可能出现 **思考过程** 或 **工具调用提示**（取决于通道与配置），等待结束即可。  
3. 若模型建议的规划过长，你仍可用点命令（第 7 章）查看状态、切换会话等。  
4. **规划 / 执行** 对用户而言不必深究：可理解为「先想清楚步骤，再逐步做完」。

---

## 7. 点命令（`.`）速查

多数以下命令在 **CLI 与飞书** 中均可使用（前缀为英文句点 `.`）。**`.schedule` 的 add/update/remove/enable/disable** 及部分 **`.session` 变异** 仅允许在本机 CLI 执行（见第 8 章）。**完整说明、示例输出与边界情况** 见 [CLI.md](CLI.md)。

### 7.1 最常用命令（速查）

| 命令 | 作用 |
|------|------|
| `.help` | 显示帮助 |
| `.status` | 查看运行状态（不中断当前执行） |
| `.session list` / `.session switch <id>` | 列出 / 切换会话 |
| `.feishu start` / `.feishu stop` / `.feishu status` | 飞书 WebSocket 长连接控制 |
| `.schedule list` | 查看定时任务（增删改见第 8 章，须在本地 CLI） |
| `.bind status` | 通道绑定状态，见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md) |

**完整命令表、示例输出与边界情况** → [CLI.md](CLI.md)。

### 7.2 使用提示

- 命令前必须是 **`.`**（句点），后面跟子命令与参数，中间空格按 [CLI.md](CLI.md) 示例。  
- 不确定时先 `.help` 或 `.status`。

---

## 8. 定时任务

在 **本地 CLI** 中可用点命令 **`.schedule`** 管理持久化定时任务：到达时间后，进程会像普通聊天一样把一轮 Agent 请求放进 **消息队列**，再进入与手动输入相同的执行路径。任务保存在 **`MINI_AGENT_STATE/scheduled_tasks/tasks.json`**（未设置 `MINI_AGENT_STATE` 时一般为仓库下 `workspaces/scheduled_tasks/`；该目录不宜提交到 Git，见 [ENGINEERING.md](ENGINEERING.md) §3.1）。

**新手要点**：

- 先 **`.schedule`** 或 **`.schedule list`** 查看子命令；语法与示例 → [CLI.md](CLI.md)。
- **调度**：`every <秒>`、`once <ISO8601>`、五段 **`cron "分 时 日 月 周"`**；`add` 的长 prompt 须用 **` -- `** 与选项分隔。
- **时区**：cron 墙钟以 `tasks.json` 的 `schedule.timezone` 为准；未写 `--tz` 时新建默认 **`MINIAGENT_SCHEDULE_TIMEZONE` → `MINIAGENT_TIMEZONE` → `TZ` → `Asia/Shanghai`**。**Agent 每轮本地时间**由 `process_timezone()` 注入（读 `MINIAGENT_TIMEZONE` / `TZ`，**不**读 `MINIAGENT_SCHEDULE_TIMEZONE`）。遗留 `timezone: UTC` 可用 **`.schedule align-tz`**。
- **飞书**：默认仅 **list** / **show**；增删改须在本地 CLI。`primary` 任务在私聊已绑定时可镜像到飞书（`MINIAGENT_SCHEDULE_FEISHU_MIRROR=0` 可关）。

退避、漏跑、工具接口与数据流 → [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」、[.env.example](../.env.example)。

---

## 9. 会话与多会话

- **会话**就像「不同的聊天窗口」，历史与部分配置相互隔离。  
- 使用 `.session list` 查看列表；在 **本地 CLI** 用 `.session switch` 切换到工作上下文。  
- **飞书里**（默认）发送 `.session switch` / `create` / `rename` 等变异子命令**不会**修改与 CLI 共享的 `active_session_id` 或会话存储，仅返回提示；请在本地终端执行，或设置 **`MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1`**（见 [FEISHU.md](FEISHU.md)、[CLI.md](CLI.md)）。  
- 会话与记忆落盘位置受 **`MINI_AGENT_STATE`** 控制，详见第 14 章与 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。

---

## 10. 飞书（可选）

1. 安装依赖：`pip install -e ".[feishu]"`。  
2. 在飞书开放平台创建企业自建应用，获取 **App ID**、**App Secret**、事件订阅与权限按 [FEISHU.md](FEISHU.md) 操作。  
3. 将凭证填入 `.env`（勿泄露）。  
4. 启动 `python -m miniagent --feishu` 或在 CLI 中 `.feishu start`。  

**入站锁**：同一状态根下通常只允许一个进程持有飞书入站连接，避免重复收消息；细节见 [FEISHU.md](FEISHU.md) 与 [SECURITY.md](SECURITY.md)。

**可选内置工具**：`.env` 中 `MINIAGENT_FEISHU_TOOLS=1` 时注册发文件、撤回、建文档、读 Markdown、列云盘、追加文档正文等工具；或 **未设置** `MINIAGENT_FEISHU_TOOLS` 时默认由 `MINIAGENT_FEISHU_TOOLS_AUTO`（且已配置 `FEISHU_APP_ID`/`SECRET`）在进程启动阶段自动注册（**不**等待 `.feishu start`；详见 [FEISHU.md](FEISHU.md)）。显式 `MINIAGENT_FEISHU_TOOLS=0`/`false`/`off` 或 `MINIAGENT_FEISHU_TOOLS_AUTO=0` 可关闭。依赖开放平台权限与 [FEISHU.md](FEISHU.md) 自检清单（含 `receive_id_type`、默认 `folder_token`、可选 `MINIAGENT_FEISHU_DOCX_URL_PREFIX`）。

**环境变量迁移**：见第 4 章「升级迁移提示」与 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`。

**工作区路径**：通过飞书发附件时，工具只能访问当前会话 **`files/`** 目录下的相对路径；飞书入站附件保存在 `files/feishu_incoming/`，详见 [FEISHU.md](FEISHU.md)「飞书与会话工作区文件」。

## 11. 联网搜索与浏览器工具（可选）

- **联网搜索（Tavily）**：在 `.env` 配置 `TAVILY_API_KEY` 或 `WEB_SEARCH_API_KEY`。未配置时，若模型尝试调用搜索工具，会得到 **明确错误提示**，不影响其它工具。  
- **浏览器正文抽取**：需 `[browser]` 与 Playwright 浏览器安装；用于部分需渲染的网页。  

超时等变量见 [.env.example](../.env.example)。

---

## 12. 技能与 ClawHub（可选）

- 默认技能根目录为仓库下 **`workspaces/skills/`**（旧版若使用根目录 `skills/`，请迁移或设置 `MINI_AGENT_SKILLS`）。  
- **内置基线**：仓库预置 **`skill-creator`**（来自 [anthropics/skills](https://github.com/anthropics/skills)，含 `LICENSE.txt`）；**`skill-vetter`**（安全审查）位于 `miniagent/skills/templates/skill-vetter/`，首次使用时可通过 `miniagent install-skill skill-vetter` 或手动复制到 `workspaces/skills/` 加载。  
- **仅从 PyPI 安装 wheel**（无完整仓库树）时，默认路径下可能没有预置技能文件；需要基线时请克隆仓库、editable 安装，或手动复制 `workspaces/skills/skill-creator`，详见 [README.md](../README.md)「技能目录迁移」。  
- **扩展**：可从 ClawHub 安装更多技能包，引导脚本见 `scripts/bootstrap_clawhub_skills.py`（参数以官方技能页为准；脚本仅为额外安装，不替代内置基线）。  
- 目录说明见 [workspaces/skills/README.md](../workspaces/skills/README.md)；深入说明见 [README.md](../README.md) 技能章节与 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) 中与技能相关的部分。

---

## 13. MCP 工具（可选）

1. `pip install -e ".[mcp]"`。  
2. 在 `.env` 中设置 `MINIAGENT_MCP_STDIO` 为 **JSON 数组** 形式的启动命令，例如文档中展示的 `["npx","-y","@组织/包名"]` 形态（请替换为你信任的 MCP 服务）。  
3. 重启进程后，工具会注册进同一工具列表。  

具体键名与注释见 [.env.example](../.env.example)。

---

## 14. 状态目录、备份与 Git

### 14.1 默认布局

未设置 `MINI_AGENT_STATE` 时，进程常把状态写在项目下的 **`workspaces/`**（实例、会话、锁、飞书去重、记忆索引等）。可通过环境变量把整棵状态树迁到其它磁盘路径，便于备份或多副本隔离。

### 14.2 哪些不应提交到 Git

根目录 `.gitignore` 已忽略多数运行时目录与文件（如 `workspaces/sessions/`、`workspaces/scheduled_tasks/`、`workspaces/memory/`、`workspaces/feishu/`、`keyword-index.json` 等）。**不要** 强行把含隐私对话或密钥的文件 `git add` 进去。政策说明见 [ENGINEERING.md](ENGINEERING.md) §3.1。

### 14.3 备份建议

若 `MINI_AGENT_STATE` 指向重要数据目录，请用你自己的备份方案（加密盘、权限控制、定期拷贝）。详见 [DEPLOYMENT.md](DEPLOYMENT.md) 与 [SECURITY.md](SECURITY.md)。

---

## 15. 常见问题（FAQ）

| 现象 | 建议 |
|------|------|
| 启动报错与 API 密钥相关 | 检查 `.env` 是否在项目根、`OPENAI_API_KEY` 是否已填且无多余引号空格；勿把密钥发到公共论坛。 |
| 无法联网查天气/新闻 | 配置 Tavily 相关变量；或接受「未配置则工具返回错误」的设计。 |
| 飞书无响应 | 查 `.feishu status`、凭证、事件订阅、是否另一进程已占入站锁；见 [FEISHU.md](FEISHU.md)。 |
| 磁盘里会话太多 | 用 `.session` 管理或迁移 `MINI_AGENT_STATE`；理解历史与归档见 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。 |
| 怀疑卡住 | `.status`；必要时查看日志级别 `AGENT_DEBUG`。 |

---

## 16. 安全与隐私清单

1. **`.env`** 仅本机保存，权限收紧；勿提交 Git。  
2. **不要在截图、录屏、聊天里** 暴露完整密钥或企业内部令牌。  
3. **共享电脑**：使用独立用户目录与独立 `MINI_AGENT_STATE`，用完可删除状态目录。  
4. **工具能力**：文件与命令受沙箱等约束，见 [SECURITY.md](SECURITY.md)；不要给不可信人员开放你的运行环境。  
5. **备份介质**：会话与记忆可能含敏感业务文本，备份同样需加密与访问控制。

---

## 17. 进阶阅读与开发

- 参与开发与代码规范：[CONTRIBUTING.md](CONTRIBUTING.md)  
- 仓库卫生、CI、单一事实来源：[ENGINEERING.md](ENGINEERING.md)  
- 架构与数据流：[ARCHITECTURE.md](ARCHITECTURE.md)  
- 部署与运维：[DEPLOYMENT.md](DEPLOYMENT.md)

普通用户日常使用 **读到第 16 章即可**；开发贡献请读 [CONTRIBUTING.md](CONTRIBUTING.md) / [ENGINEERING.md](ENGINEERING.md)，并见 **第 18 章** [进阶与维护](#user-guide-sec18-advanced) 表。

---

## 18. 文档索引

**完整专题列表与目录树**以 [INDEX.md](INDEX.md) 为准；下表为常用入口。

| 文档 | 适合 |
|------|------|
| [README.md](../README.md) | 项目概览与最短命令 |
| [INDEX.md](INDEX.md) | 全部文档导航 |
| [CLI.md](CLI.md) | 点命令全集与示例 |
| [FEISHU.md](FEISHU.md) | 飞书集成 |
| [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) | 记忆与子系统 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 安装与部署 |
| [SECURITY.md](SECURITY.md) | 安全模型与清单 |
| [ENGINEERING.md](ENGINEERING.md) | 多实例与注册表（§3.3）、质量门禁 |
| [SELF_OPT.md](SELF_OPT.md) | 自我优化 |
| [CHANNEL_BINDING.md](CHANNEL_BINDING.md) | 通道绑定 |
| [examples/README.md](examples/README.md) | 脱敏配置示例说明 |

<a id="user-guide-sec18-advanced"></a>

### 进阶与维护（贡献者 / CI）

| 文档 | 适合 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层架构与数据流 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、编码与测试约定 |
| [ENGINEERING.md](ENGINEERING.md) | 质量门禁、CI、`.gitignore` 与单一事实来源 |
| [PERFORMANCE.md](PERFORMANCE.md) | 性能合成冒烟、基线与剖析 |
| [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md) | CLI/飞书输出格式规范、流式输出、间距规则 |

---

**结语**：按第 3～5 章完成安装与启动后，建议先熟悉 **自然语言提问** 与 **`.help` / `.status` / `.session list`**，再按需打开飞书、搜索与技能。遇到问题优先查第 15 章 FAQ 与对应专题文档。
