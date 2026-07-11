# Mini Agent Python — 全项目使用指南（新手向）

> 本文面向 **零基础或仅有普通电脑使用经验** 的读者，按顺序操作即可完成安装、配置与日常使用。  
> 技术细节与命令全集以专题文档为准；本文提供 **路径导航 + 常见操作 + 安全习惯**。  
> Mini Agent Python | 版本: 2.1.0 | 最后更新: 2026-07-11 | 与 `miniagent.__version__` 对齐 | 未发版行为见 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`

---

## 目录

1. [前言：本项目能做什么](#1-前言本项目能做什么)
2. [开始前准备](#2-开始前准备)
3. [获取代码与安装](#3-获取代码与安装)
4. [快速入门（5分钟体验）](#4-快速入门5分钟体验)
5. [首次配置（JSON 配置文件）](#5-首次配置json-配置文件)
6. [第一次启动与退出](#6-第一次启动与退出)
7. [日常对话怎么用](#7-日常对话怎么用)
8. [点命令（`/`）速查](#8-点命令速查)
9. [定时任务（/schedule）](#9-定时任务)
10. [会话与多会话](#10-会话与多会话)
11. [飞书（可选）](#11-飞书可选)
12. [联网搜索与浏览器工具（可选）](#12-联网搜索与浏览器工具可选)
13. [技能与 ClawHub（可选）](#13-技能与-clawhub可选)
14. [知识库（/kb）](#14-知识库kb)
15. [MCP 工具（可选）](#15-mcp-工具可选)
16. [状态目录、备份与 Git](#16-状态目录备份与-git)
17. [常见问题（FAQ）](#17-常见问题faq)
18. [安全与隐私清单](#18-安全与隐私清单)
19. [进阶阅读与开发](#19-进阶阅读与开发)
20. [文档索引](#20-文档索引)（含 [进阶与维护](#user-guide-sec20-advanced) 专题链）

---

## 1. 前言：本项目能做什么

**Mini Agent Python** 是一个在本地（或你自己的服务器）上运行的 **智能助手程序**。它通过 **大语言模型（LLM）** 理解你的文字需求，并可在授权范围内 **调用工具**（例如读写工作区文件、执行命令、联网搜索等），尽量自动完成任务。

与「只能聊天」的网页机器人相比，典型差异包括：

| 能力 | 说明 |
|------|------|
| **多阶段** | 先 **需求澄清**（三步法）再 **规划** 再 **执行**（你可从回复节奏上感知：先有条理再动手），简单任务可跳过规划直接执行。细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。 |
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

- **至少一种 LLM API**：程序通过 **OpenAI 兼容接口** 调用模型。你会有「API 地址」和「API 密钥」两个概念；密钥 **只应** 出现在本机 `config.user.json` 的 `secrets` 部分或环境变量里，**不要** 写进聊天截图或发给陌生人。
- **可选**：飞书企业自建应用、Tavily 搜索密钥、Playwright 浏览器、MCP 服务等——用到再配置即可。

具体配置项见下一章与仓库根目录的 `config.defaults.json`。

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

## 4. 快速入门（5分钟体验）

> 如果你已经完成安装，想先快速体验再细读配置章节，按以下步骤操作即可。

### 4.1 最小配置

1. 复制配置模板：

   ```bash
   cp config.defaults.json config.user.json
   ```

2. 编辑 `config.user.json`，填入必要的 API 密钥：

   ```json
   {
     "secrets": {
       "openai_api_key": "你的密钥"
     }
   }
   ```

### 4.2 启动并对话

```bash
python -m miniagent
```

看到 `>>>` 提示符后，输入：

```
帮我列出当前目录的文件
```

Agent 会自动调用文件工具完成任务。

### 4.3 常用操作

| 操作 | 命令/方法 |
|------|----------|
| 查看状态 | `/status` |
| 切换会话 | `/session switch <id>` |
| 退出程序 | `/exit` 或 `Ctrl+D` |
| 查看帮助 | `/help` |

### 4.4 下一步

- 详细配置选项见 [第5章：首次配置](#5-首次配置json-配置文件)
- CLI 命令全集见 [CLI.md](CLI.md)
- 飞书集成见 [第11章：飞书](#11-飞书可选)

---

## 5. 首次配置（JSON 配置文件）

**升级迁移提示**（详见 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` Breaking）：飞书出站默认 `feishu.reply_target=reply`；内置飞书工具默认由 `feishu.tools_auto` 注册；配置已全部迁移到 JSON（`config.user.json`）；云文档链接与文件夹见 `feishu.doc.docx_url_prefix`、`feishu.doc.folder_token`。飞书细节见第 11 章。

### 5.1 创建配置文件

在 **项目根目录**（与 `pyproject.toml` 同级）执行：

```bash
cp config.defaults.json config.user.json
```

用任意文本编辑器打开 `config.user.json`，填写敏感凭据和个性化配置。

**不要** 把填好密钥的 `config.user.json` 上传到公开仓库；仓库已默认在 `.gitignore` 中忽略此文件。

**首次配置引导**：如果首次启动时检测到无 `config.user.json`，CLI 会自动进入交互式配置引导：

```
🚀 MiniAgent 首次配置

请输入 OpenAI API 密钥: sk-xxxxx
请输入模型名称 (默认 gpt-4o-mini): 
请输入 API 端点 (默认 https://api.openai.com/v1): 
请输入工作目录 (默认 workspaces): 

✅ 配置已保存到 config.user.json
```

配置完成后会自动生成 `config.user.json`，无需手动复制模板。

### 5.2 必填（最小可运行）

在 `config.user.json` 的 `secrets` 部分填写 API 密钥：

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
| `secrets.openai_api_key` | 你的 API 密钥（由服务商控制台生成；**勿**在文档或截图中泄露）。 |
| `model.base_url` | 可选。使用官方或兼容网关时按服务商说明填写。 |
| `model.model` | 可选。默认可用服务商推荐的模型 id。 |

### 5.3 常用可选配置（摘选）

完整配置项以 `config.defaults.json` 为准。下表仅列新手常问的项：

| 配置路径 | 用途 |
|------|------|
| `model.temperature` | 模型温度，默认 0.7 |
| `model.thinking_level` | 思考档位：`light` / `medium` / `heavy` |
| `agent.max_turns` | 单轮 ReAct 最大轮数，**默认 400** |
| `agent.debug` | `true` 时更啰嗦的日志；日常可 `false` |
| `secrets.tavily_api_key` | 启用联网搜索（Tavily） |
| `secrets.feishu_app_id` / `secrets.feishu_app_secret` | 飞书应用凭证 |
| `paths.state_dir` | 状态根目录，默认 `workspaces` |

### 5.4 配置分层说明

`config.defaults.json` 顶部 `_config_guide` 列出 **User 层**与 **Advanced 层**节名。普通用户只需在 `config.user.json` 覆盖 User 层键；Advanced 节（`memory`、`trace`、`dream`、`self_optimization`）一般保持默认。

运行时优先级：**config.user.json > config.defaults.json**（不支持 `MINIAGENT_*` 环境变量覆盖）。

### 5.5 从旧版本迁移

若曾使用 `.env` 或 `MINIAGENT_*` 环境变量：

1. `cp config.defaults.json config.user.json`
2. 将凭据写入 `secrets`（如原 `OPENAI_API_KEY` → `secrets.openai_api_key`）
3. 将其余项映射到对应 JSON 节（见 `config.defaults.json` 字段结构）
4. 删除或归档旧 `.env` 文件

---

## 6. 第一次启动与退出

### 6.1 仅终端（CLI）

```bash
python -m miniagent
```

继续上次 CLI 会话（状态存于 `channel-router.json`）：

```bash
python -m miniagent --continue
```

指定会话启动：

```bash
python -m miniagent --session <会话ID>
```

看到欢迎信息后，可直接用 **自然语言** 输入需求。部分环境也可用 `quit` / `exit` 退出（与 [CLI.md](CLI.md) 一致）。

### 6.2 终端 + 飞书同时启动

需已安装 `[feishu]` 并配置飞书环境变量：

```bash
python -m miniagent --feishu
python -m miniagent --feishu --continue   # 同时恢复上次 CLI 会话
```

飞书 **不会** 单独占一个无终端的进程；始终与 CLI 主循环一起。更多见第 11 章与 [FEISHU.md](FEISHU.md)。

### 6.3 多实例与停止其它进程

```bash
python -m miniagent --stop
```

用于列出本机已注册实例并交互停止；`--stop --all` 或 `--stop 1 2` 等用法见 [README.md](../README.md) 与 [ENGINEERING.md](ENGINEERING.md) §3.3。  
若从不同目录启动过旧实例，`--stop` 会聚合列出多个状态根（表格含「状态目录」列）；同 ID 跨根时需 `--stop --state-dir <路径> <id>`。  
说明：清理的是 **注册信息** 与 **你选择的进程**；不要随意结束他人机器上的进程。

---

## 7. 日常对话怎么用

1. 启动后，在提示处 **直接输入中文或英文需求** 即可。  
2. 若任务需要工具，界面可能出现 **思考过程** 或 **工具调用提示**（取决于通道与配置），等待结束即可。  
3. 若模型建议的规划过长，你仍可用点命令（第 8 章）查看状态、切换会话等。  
4. **规划 / 执行** 对用户而言不必深究：可理解为「先想清楚步骤，再逐步做完」。

---

## 8. 点命令（`/`）速查

多数以下命令在 **CLI 与飞书** 中均可使用（前缀为斜杠 `/`）。**`/schedule` 的 add/update/remove/enable/disable** 仅允许在本机 CLI 执行（见第 9 章）；部分 **`/session` 变异** 亦仅允许在本机 CLI（见第 10 章）。**完整说明、示例输出与边界情况** 见 [CLI.md](CLI.md)。

### 8.1 最常用命令（速查）

| 命令 | 作用 |
|------|------|
| `/help` | 显示帮助 |
| `/status` | 查看运行状态（含通道绑定与 CLI 聚焦模式，见 [FEISHU.md §通道绑定](FEISHU.md#通道绑定)；不中断当前执行） |
| `/session list` / `/session switch <id>` | 列出 / 切换会话（切换会同步 CLI 与自动私聊绑定） |
| `/feishu start` / `/feishu stop` / `/feishu status` | 飞书 WebSocket 长连接控制 |
| `/schedule list` | 查看定时任务（增删改见第 9 章，须在本地 CLI） |
| `/reload-config` | 重新加载配置文件（热更新） |
| `/config [section]` | 查看配置概览；指定 section 时查看该部分 |
| `/model [name]` | 显示当前模型；指定 name 时切换模型 |
| `/doctor` | 诊断安装与配置 |

**完整命令表、示例输出与边界情况** → [CLI.md](CLI.md)。

### 8.2 使用提示

- 命令前必须是 **`/`**（斜杠），后面跟子命令与参数，中间空格按 [CLI.md](CLI.md) 示例。
- 不确定时先 `/help` 或 `/status`。
- **模糊匹配**：输入错别字时系统会提示"您是否想输入 xxx？"，如 `/sttatus` → 提示 `/status`。
- **Tab 补全**：输入 `/` 命令或 `@file:` 文件路径时按 `Tab` 键自动补全。

---

## 9. 定时任务

在 **本地 CLI** 中可用点命令 **`/schedule`** 管理持久化定时任务：到达时间后，进程会像普通聊天一样把一轮 Agent 请求放进 **消息队列**，再进入与手动输入相同的执行路径。任务保存在 **`{paths.state_dir}/scheduled_tasks/tasks.json`**（默认 `workspaces/scheduled_tasks/`；该目录不宜提交到 Git，见 [ENGINEERING.md](ENGINEERING.md) §3.1）。

**新手要点**：

- 先 **`/schedule`** 或 **`/schedule list`** 查看子命令；语法与示例 → [CLI.md](CLI.md)。
- **调度**：`every <秒>`、`once <ISO8601>`、五段 **`cron "分 时 日 月 周"`**；`add` 的长 prompt 须用 **` -- `** 与选项分隔。
- **时区**：见 [CLI.md §/schedule](CLI.md)「时区」说明（SSOT）。
- **飞书**：默认仅 **list** / **show**；增删改须在本地 CLI。`primary` 任务在私聊已绑定时可镜像到飞书（`scheduled_tasks.feishu_mirror=false` 可关）。

退避、漏跑、工具接口与数据流 → [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」、`config.defaults.json` 的 `scheduled_tasks` 节。

---

## 10. 会话与多会话

- **会话**就像「不同的聊天窗口」，历史与部分配置相互隔离。  
- 使用 `/session list` 查看列表；在 **本地 CLI** 用 `/session switch` 切换到工作上下文。  
- **飞书里**（默认）发送 `/session switch` / `create` / `rename` 等变异子命令**不会**修改与 CLI 共享的 `active_session_id` 或会话存储，仅返回提示；请在本地终端执行，或设置 **`feishu.dot_commands_full=true`**（见 [FEISHU.md](FEISHU.md)、[CLI.md](CLI.md)）。  
- 会话与记忆落盘位置由 **`paths.state_dir`** 控制，详见第 16 章与 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。

---

## 11. 飞书（可选）

1. 安装依赖：`pip install -e ".[feishu]"`。  
2. 在飞书开放平台创建企业自建应用；App ID、App Secret、事件订阅与权限见 **[FEISHU.md](FEISHU.md) §快速开始**（SSOT）。  
3. 将凭证填入 `config.user.json` 的 `secrets` 部分（勿泄露）。  
4. 启动 `python -m miniagent --feishu` 或在 CLI 中 `/feishu start`。  

**通道绑定**（CLI 与飞书私聊共享会话）、入站锁、内置工具、附件路径等运维细节见 [FEISHU.md](FEISHU.md)（含 [§通道绑定](FEISHU.md#通道绑定)）与 [SECURITY.md](SECURITY.md)。升级迁移见第 4 章「升级迁移提示」与 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`。

## 12. 联网搜索与浏览器工具（可选）

- **联网搜索（Tavily）**：在 `config.user.json` 的 `secrets` 部分配置 `tavily_api_key` 或 `web_search_api_key`。未配置时，若模型尝试调用搜索工具，会得到 **明确错误提示**，不影响其它工具。  
- **浏览器正文抽取**：需 `[browser]` 与 Playwright 浏览器安装；用于部分需渲染的网页。  

超时等见 `config.defaults.json` 的 `agent` 节（如 `agent.tool_timeout`）。

---

## 13. 技能与 ClawHub（可选）

- 默认技能根目录为仓库下 **`workspaces/skills/`**（可在 `config.user.json` 设置 `paths.skills_dir`）。  
- **内置基线**：仓库预置 **`skill-creator`**（来自 [anthropics/skills](https://github.com/anthropics/skills)，含 `LICENSE.txt`）；**`skill-vetter`**（安全审查）位于 `miniagent/skills/templates/skill-vetter/`，首次使用时可通过 `miniagent install-skill skill-vetter` 或手动复制到 `workspaces/skills/` 加载。  
- **仅从 PyPI 安装 wheel**（无完整仓库树）时，默认路径下可能没有预置技能文件；需要基线时请克隆仓库、editable 安装，或手动复制模板目录（见 [workspaces/skills/THIRD_PARTY_SKILLS.md](../workspaces/skills/THIRD_PARTY_SKILLS.md)）。  
- **扩展**：可从 ClawHub 安装更多技能包，引导脚本见 `scripts/bootstrap_clawhub_skills.py`（参数以官方技能页为准；脚本仅为额外安装，不替代内置基线）。  
- 第三方许可清单与合规说明见 **[workspaces/skills/THIRD_PARTY_SKILLS.md](../workspaces/skills/THIRD_PARTY_SKILLS.md)**（SSOT）。

---

## 14. 知识库（/kb）

将本地文档挂载入 Agent，对话时自动检索相关内容拼入上下文。示例：`/kb mount ./my-docs 手册` → `/kb search 部署流程 手册`。

完整目录结构、Agent 工具与全部子命令见 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)；命令示例见 [CLI.md](CLI.md) `/kb` 节。

---

## 15. MCP 工具（可选）

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

具体配置见 `config.defaults.json` 的 `mcp` 节与 [ENGINEERING.md](ENGINEERING.md) §1。

---

## 16. 状态目录、备份与 Git

### 16.1 默认布局

默认布局下，**项目业务状态**（会话、锁、飞书去重、记忆索引等）写入 miniagent 安装/源码根下的 **`workspaces/projects/{project_key}/`**（按启动 cwd 自动区分）；**实例注册表** 仍在 `workspaces/instances/`。若 cwd 下存在旧版 `{cwd}/workspaces/` 数据，legacy 回退会继续使用该路径。可在 `config.user.json` 设置绝对 `paths.state_dir` 或通过 `MINIAGENT_PATHS_STATE_DIR` 将项目数据迁到其它磁盘路径，便于备份或多副本隔离。

### 16.2 哪些不应提交到 Git

根目录 `.gitignore` 已忽略多数运行时目录与文件（如 `workspaces/sessions/`（即 `{paths.state_dir}/sessions/`，见 [ENGINEERING.md](ENGINEERING.md) §3）、`workspaces/scheduled_tasks/`、`workspaces/memory/`、`workspaces/feishu/`、`keyword-index.json` 等）。**不要** 强行把含隐私对话或密钥的文件 `git add` 进去。政策说明见 [ENGINEERING.md](ENGINEERING.md) §3.1。

### 16.3 备份建议

若 `paths.state_dir` 指向重要数据目录，请用你自己的备份方案（加密盘、权限控制、定期拷贝）。详见 [DEPLOYMENT.md](DEPLOYMENT.md) 与 [SECURITY.md](SECURITY.md)。

---

## 17. 常见问题（FAQ）

速查表；详细排障步骤见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

| 现象 | 建议 |
|------|------|
| 启动报错与 API 密钥相关 | 检查 `config.user.json` 是否在项目根、`secrets.openai_api_key` 是否已填且无多余引号空格；详见 [TROUBLESHOOTING.md §启动问题](TROUBLESHOOTING.md#启动问题)。 |
| 无法联网查天气/新闻 | 配置 Tavily 相关变量；或接受「未配置则工具返回错误」的设计。详见 [TROUBLESHOOTING.md §配置问题](TROUBLESHOOTING.md#配置问题)。 |
| 飞书无响应 | 查 `/feishu status`、凭证、事件订阅、是否另一进程已占入站锁；详见 [FEISHU.md](FEISHU.md) 与 [TROUBLESHOOTING.md §飞书](TROUBLESHOOTING.md#飞书集成问题)。 |
| 磁盘里会话太多 / 内存占用高 | 用 `/session` 管理或调整 `paths.state_dir`；调优见 [PERFORMANCE.md Part B](PERFORMANCE.md#part-b--运行时调优)；诊断见 [TROUBLESHOOTING §性能问题](TROUBLESHOOTING.md#性能问题)。 |
| 怀疑卡住 / 响应缓慢 | `/status`；必要时 `AGENT_DEBUG=1`。详见 [TROUBLESHOOTING §运行问题](TROUBLESHOOTING.md#运行问题) 与 [§性能问题](TROUBLESHOOTING.md#性能问题)。 |

---

## 18. 安全与隐私清单

1. **`config.user.json`** 仅本机保存，权限收紧；勿提交 Git。  
2. **不要在截图、录屏、聊天里** 暴露完整密钥或企业内部令牌。  
3. **共享电脑**：使用独立用户目录与独立 `paths.state_dir`，用完可删除状态目录。  
4. **工具能力**：文件与命令受沙箱等约束，见 [SECURITY.md](SECURITY.md)；不要给不可信人员开放你的运行环境。  
5. **备份介质**：会话与记忆可能含敏感业务文本，备份同样需加密与访问控制。

---

## 19. 进阶阅读与开发

- 参与开发与代码规范：[CONTRIBUTING.md](CONTRIBUTING.md)  
- 仓库卫生、CI、单一事实来源：[ENGINEERING.md](ENGINEERING.md)  
- 架构与数据流：[ARCHITECTURE.md](ARCHITECTURE.md)  
- 部署与运维：[DEPLOYMENT.md](DEPLOYMENT.md)  
- 自我优化（提案与 Trace 分析）：[SELF_OPT.md](SELF_OPT.md)

普通用户日常使用 **读到第 17 章即可**；开发贡献请读 [CONTRIBUTING.md](CONTRIBUTING.md) / [ENGINEERING.md](ENGINEERING.md)，并见 **第 20 章** [文档索引](#user-guide-sec20-advanced) 表。

---

## 20. 文档索引

**完整专题列表、按角色导航、SSOT 对照与项目目录树**以 [INDEX.md](INDEX.md) 为准。

<a id="user-guide-sec20-advanced"></a>

贡献者与维护者路径（架构、工程、性能、输出格式、提示词规范等）见 [INDEX.md](INDEX.md)「按角色导航」。

---

**结语**：按第 3～5 章完成安装与启动后，建议先熟悉 **自然语言提问** 与 **`/help` / `/status` / `/session list`**，再按需打开飞书、知识库、搜索与技能。遇到问题优先查第 17 章 FAQ 与 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。
