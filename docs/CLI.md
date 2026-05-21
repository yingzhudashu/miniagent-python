# CLI 命令手册

> Mini Agent Python | 版本: 2.0.2 | 多数 `.` 命令在 CLI 与飞书均可使用；`.schedule` 的 add/update/remove/enable/disable 及部分 `.session` 变异仅允许在本机 CLI 执行

在 **本地 CLI** 执行 `.session switch` 时，会同步更新 **CLI 通道绑定** 与已自动跟随的 **飞书私聊 sender**，使二者与 `active_session_id` 一致（在飞书内发 `.session switch` / `create` / `rename` 等变异子命令不会修改共享状态，见 [FEISHU.md](FEISHU.md)）。飞书多实例场景下，仅一个进程可持有入站连接（见 `feishu_inbound_owner.json`）。

## 启动命令

```bash
python -m miniagent                     # CLI 模式（默认）
python -m miniagent --feishu            # CLI + 飞书同时启动
python -m miniagent --stop              # 列出运行中实例；交互选择停止
python -m miniagent --stop --all        # 停止全部
python -m miniagent --stop 1 2          # 停止指定 ID
```

## 终端 Markdown（Rich，可选）

全屏 CLI（prompt_toolkit TUI）下，上方 transcript 中 **Assistant 最终回复** 在已安装 **`pip install -e ".[cli]"`** 时由 Rich 将 Markdown（含常见表格）渲染为彩色 ANSI。未安装则显示原始 Markdown 文本。

- **`MINIAGENT_CLI_RAW_MARKDOWN=1`**：强制关闭回复区 Rich。
- **`MINIAGENT_CLI_THINKING_RICH=1`**：对**非流式**思考块尝试 Rich；**流式**输出的规划/执行过程仍为纯文本；与工具行合并（`merge_tools`）的短行仍为纯文本。
- 默认 **`MINIAGENT_THINKING_MERGE_TOOLS`**（非 `0` 即开启）时，同一 `thinking_header`（如 `[步骤 i/n]`、`[执行]`）内：工具意图行会接在当前流式块后，**不另起新的「步骤」标签**，且继续流式时不会整段重打上一子轮正文（与飞书同卡 PATCH 语义一致）。关闭合并则工具行单独成块。流式 **header 切换**（如规划 → 执行）时无论是否启用飞书都会收尾并重置流式状态。
- 渲染宽度与滚动条占用与主循环一致，便于表格与回复区对齐。

详见 [README.md](../README.md) 与 [USER_GUIDE.md](USER_GUIDE.md) §4.3。

## 命令总览

| 分类 | 命令 | 说明 |
|------|------|------|
| **状态** | `.status` | 检查 Agent 状态（不中断执行） |
| **会话** | `.session list` | 列出所有会话 |
| | `.session switch <编号/ID>` | 切换会话 |
| | `.session create <ID> [标题]` | 创建新会话 |
| | `.session rename <编号/ID> <新标题>` | 重命名会话 |
| **实例** | `.instance list` | 列出运行实例 |
| | `.instance stop <ID>` | 停止指定实例 |
| **飞书** | `.feishu start` | 启动飞书连接 |
| | `.feishu stop` | 停止飞书连接 |
| | `.feishu status` | 查看飞书状态 |
| **绑定** | `.bind status` | 查看通道绑定状态 |
| | `.bind cli <会话>` | CLI 绑定到指定会话 |
| | `.bind feishu <sender> <会话>` | 飞书私聊绑定到指定会话 |
| | `.unbind cli` | 解除 CLI 绑定 |
| | `.unbind feishu <sender>` | 解除飞书私聊绑定 |
| | `.unbind all` | 解除所有绑定 |
| **队列** | `.queue status` | 查看消息队列状态 |
| | `.queue set <模式>` | 切换 queue / preemptive |
| | `.queue abort` / `.abort` | 中止本 `chat_id` 上 `dispatch` / `dispatch_wait` 投递的任务（非 `.stop`） |
| **定时任务** | `.schedule` | 无参或与 `list` 相同：列出用法与子命令 |
| | `.schedule list` | 列出所有定时任务 |
| | `.schedule show <id>` | 打印任务 JSON |
| | `.schedule add …` | 新增 interval / once / cron 任务（须含 ` -- ` 分隔 prompt） |
| | `.schedule update <id> …` | 修改已有任务（语法同 add） |
| | `.schedule remove|enable|disable <id>` | 删除 / 启用 / 禁用 |
| **模型** | `.profile <名称>` | 切换模型预设 |
| **统计** | `.stats` | 工具调用统计 |
| **控制** | `.stop` | 停止当前实例并退出 |
| **技能** | `.reload-skills` | 从磁盘全量重新加载 `workspaces/skills`（`install_skill` 成功后通常已自动热加载） |
| **帮助** | `.help` | 显示帮助信息 |
| | `quit` / `exit` | 退出程序 |

## 命令详解

### .status — Agent 状态检查

**不中断执行**，用于怀疑 Agent 卡死时的诊断。

```
> .status

🏭 实例: #1
📁 当前会话: #1 default
💬 飞书: 🟢 运行中

📬 消息队列:
  模式: 🟢 queue
  oc_xxxxx: 🔴 处理中 (45.2s)
    等待: 2 条
  CLI: ⚪ 空闲
```

### .session — 会话管理

所有会话命令同时支持**编号**和**原始 ID**。

```
> .session list

📋 会话列表:
  - #1 default ← 当前 | 5 轮 🔒 (本实例)
  - #2 cli-interactive | 3 轮
  - #3 oc_3a135408 | 1 轮

> .session switch 2
🔄 已切换到会话: #2 cli-interactive

> .session create test-session 测试会话
✅ 已创建会话: #4 测试会话

> .session rename 4 我的测试
✅ 已重命名: #4 我的测试
```

### .instance — 多实例管理

```
> .instance list

🏭 运行实例:
  #1  PID=12345  模式=both    启动=12:30  会话=[default]  ← 当前
  #2  PID=12346  模式=cli     启动=12:35  会话=[test]

> .instance stop 2
✅ 实例 #2 已停止
```

`模式` 列：`cli` 为仅 CLI；`both` 为 CLI + 飞书连接已启用。产品仅这两种启动形态，不存在无 CLI 的独立飞书进程入口。

### .queue — 消息队列

```
> .queue status

📬 消息队列状态
  模式: 🟢 队列模式 (queue)
  oc_xxxxx: 空闲
  CLI: 空闲

> .queue set preemptive
✅ 已切换到 打断模式（最新消息打断前面处理）
```

**中止队列：**`.queue abort` 与短命令 `.abort` 会取消当前聊天室（飞书为当前群/私聊 `chat_id`，CLI 为内部 `__cli__`）上排队与执行中的任务，包括经 `dispatch_wait` 投递的回合（如部分定时任务）。不会退出进程；与 `.stop`（停实例）不同。

**两种模式：**
- `queue`（默认）：消息按顺序处理
- `preemptive`：最新消息打断当前处理

### .schedule — 定时任务

任务持久化在 **`MINI_AGENT_STATE/scheduled_tasks/tasks.json`**（未设置环境变量时一般为仓库下 `workspaces/scheduled_tasks/`）。触发时与手动输入一样经 **消息队列** 跑一轮 Agent。详见 [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」与 [USER_GUIDE.md](USER_GUIDE.md) 第 8 章。

**语法摘要**（与无参 `.schedule` 打印一致）：

```
.schedule list
.schedule show <id>
.schedule remove <id>
.schedule enable <id>   |   .schedule disable <id>
.schedule align-tz
.schedule add <id> every <秒> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
.schedule add <id> once <ISO8601> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
.schedule add <id> cron "<分> <时> <日> <月> <周>" <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
```

**要点**：

- **`add` 必须包含 ` -- `**（空格、两个连字符、空格）：前面为调度与会话参数，后面为交给模型的 **prompt**；缺少分隔符会报错。
- **`every`**：间隔秒数为正整数；**`once`**：时间为 ISO8601（可含 `Z` 或 `+08:00`）；未带时区的 naive 时间由 **`--tz`** 解释（未写时读 `MINIAGENT_SCHEDULE_TIMEZONE` → `MINIAGENT_TIMEZONE` → `TZ`，见 [.env.example](../.env.example)）。
- **飞书收结果**：飞书 WebSocket 已连接且任务为 **`primary`** 且已与飞书私聊绑定时，定时任务会镜像思考流与最终回复到飞书（`MINIAGENT_SCHEDULE_FEISHU_MIRROR=0` 关闭）；详见 [USER_GUIDE.md](USER_GUIDE.md) §8。
- **会话**：`primary` 使用当前路由的主会话 / 活跃会话；`ephemeral` 每次新建临时会话键；`fixed:会话ID` 固定到某会话（如 `fixed:default` 或 `fixed:feishu:oc_xxx`，后者可用于飞书群任务）。
- **时区**：cron 墙钟以 `tasks.json` 内 `schedule.timezone` 为准；未写 `--tz` 时新建任务默认时区为 `MINIAGENT_SCHEDULE_TIMEZONE` → `MINIAGENT_TIMEZONE` → `TZ` → `Asia/Shanghai`。遗留 `timezone: UTC` 请 **`update --tz`** 或 **`.schedule align-tz`**（批量写盘并重算 `next_run_at`）。
- **关闭调度循环**（不删任务表）：`MINIAGENT_DISABLE_SCHEDULED_TASKS=1`；dispatch 失败退避秒数：`MINIAGENT_SCHEDULE_DISPATCH_BACKOFF`（默认 60，见 [.env.example](../.env.example)）。

**飞书渠道**：在飞书里发 `.schedule` 时，通常 **仅允许** `list` / `show`；`add` / `remove` / `enable` / `disable` 须在 **本机 CLI** 执行（与 `.session` 变异限制类似）。

**Agent 工具**（可选，由环境变量控制注册）：`run_dot_command` 可执行与上文相同的点命令行；`manage_scheduled_task` 以 JSON 维护任务。见 [.env.example](../.env.example) 中 `MINIAGENT_CLI_DOT_TOOLS`、`MINIAGENT_SCHEDULE_TOOLS`。

### .bind / .unbind — 通道绑定

将 CLI 或飞书私聊绑定到同一会话，共享记忆与上下文；**飞书群聊不参与绑定**。

- `.bind status` — 查看绑定
- `.bind cli <会话>` — CLI 绑定（编号或 ID）
- `.bind feishu <sender_id> <会话>` — 飞书私聊绑定
- `.unbind cli` / `.unbind feishu <sender_id>` / `.unbind all` — 解除绑定

示例输出、自动跟随 `.session switch` 与私聊首条自动绑定等见 **[CHANNEL_BINDING.md](CHANNEL_BINDING.md)**。

### .feishu — 飞书控制

```
> .feishu start
[飞书] 正在启动 WebSocket 长轮询...
✅ 飞书已启动

> .feishu status
🟢 飞书: 运行中

> .feishu stop
✅ 飞书已停止
```

### .profile — 模型预设

```
> .profile
当前预设: balanced
可用: creative, balanced, precise, code, fast

> .profile precise
📡 已切换到预设: precise
```

## 飞书中使用命令

飞书消息以 `.` 开头时，自动路由到命令调度器而非 Agent：

- **多数**点命令（如 `.status`、`.help`、`.queue status`）可在飞书使用。
- **默认仅本机 CLI**：`.schedule` 的 `add` / `update` / `remove` / `enable` / `disable` / `align-tz`；`.session` 的 `switch` / `create` / `rename`；`.stop`（与 [USER_GUIDE.md](USER_GUIDE.md) 第 8、9 章一致）。
- **全开**：设置 `MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1` 后飞书与 CLI 点命令能力相同（见 [FEISHU.md](FEISHU.md)）。

```
飞书发送: .status
飞书回复: 🏭 实例: #1 ...

飞书发送: .help
飞书回复: (完整帮助信息)

飞书发送: 今天天气怎么样
→ 正常交给 Agent 处理
```

## 与 Agent 对话

直接输入文字即可与 Agent 对话：

```
> 帮我查一下北京天气
👤 You: 帮我查一下北京天气

🔧 web_search — 搜索北京天气
🔧 web_fetch — 抓取天气网页

🦾 Agent
  北京今天晴，气温 15-25°C...
```

## 相关文档

- [ENGINEERING.md](ENGINEERING.md)：本地与 CI 质量门禁、`MINI_AGENT_STATE`。
- [SECURITY.md](SECURITY.md)：沙箱与工具安全模型。
- [CHANNEL_BINDING.md](CHANNEL_BINDING.md)：CLI 与飞书会话绑定。
