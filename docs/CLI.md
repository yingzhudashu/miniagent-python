# CLI 命令手册

> Mini Agent Python | 版本: 2.2.0 | 最后更新: 2026-07-14 | 与 `miniagent.__version__` 对齐 | 命令使用 `/` 前缀；多数命令在 CLI 与飞书均可使用

**命令前缀**：系统统一使用 `/` 前缀（更符合CLI惯例）。例如：`/help`、`/status`、`/session list`。

在 **本地 CLI** 执行 `/session switch` 时，会同步更新 **CLI 通道绑定** 与已自动跟随的 **飞书私聊 sender**，使二者与 `active_session_id` 一致（在飞书内发 `/session switch` / `create` / `rename` 等变异子命令不会修改共享状态，见 [FEISHU.md](FEISHU.md)）。飞书多实例场景下，仅一个进程可持有入站连接（见 `feishu_inbound_owner.json`）。

## 启动命令

```bash
python -m miniagent                     # CLI 模式（默认）；无冲突时隐式等价 --continue
python -m miniagent --continue          # 继续上次 CLI 活跃会话（存于 channel-router.json）
python -m miniagent --no-continue       # 禁用隐式继续，使用 default 会话
python -m miniagent --session <ID>      # 启动并绑定到指定会话
python -m miniagent --feishu            # CLI + 飞书同时启动
python -m miniagent --feishu --continue # CLI + 飞书，并继续上次会话
python -m miniagent --stop              # 列出运行中实例；交互选择停止
python -m miniagent --stop --all        # 停止全部
python -m miniagent --stop 1 2          # 停止指定 ID
python -m miniagent --stop --state-dir <路径> 1  # 操作显式指定的实例注册表目录
```

**一目录一实例**、PID 存活判定与 `--stop --state-dir` 语义见 **[ENGINEERING.md §3.3](ENGINEERING.md#33-多实例注册表)**。

`--continue` / 隐式继续：将 `last_cli_session` 写入项目 workspace 的 `channel-router.json`（默认 `{miniagent}/workspaces/projects/<key>/channel-router.json`）；在退出（含 `quit`/`exit`、Ctrl+C、SIGTERM）、`/session switch` 时更新。下次启动会恢复该会话 ID；**全屏 CLI** 在 transcript 区加载最近历史，**简易 CLI**（无 TUI）启动时也会打印最近历史摘要；`/session switch` 后会重载对应会话历史。使用 `--no-continue` 可强制从 `default` 会话开始。

## 全屏 transcript 历史加载

全屏 CLI 启动或 `/session switch` 后，transcript 先加载最近 `memory.initial_history_count` 条会话消息；如果磁盘历史更多，顶部会显示“向上滚动加载更多历史”的提示。继续向上滚动时会按批次加载更早消息，并保持 user/assistant 轮次顺序，避免只显示答案或把问答插反。

transcript 是显示缓冲，不是历史真相源。会话历史仍保存在当前会话 workspace 的 `history.json`；显示缓冲只按 `memory.max_transcript_chars` 做字符级保护，防止长时间运行后 TUI 占用过多内存。`/copy` 在全屏 CLI 中复制当前 transcript 已加载内容；若需要完整磁盘历史，应从会话 `history.json` 或后续专门导出命令读取。

## 输入框历史（↑↓）与 transcript 滚动的区别

这是两套独立机制，不要混淆：

| 操作 | 作用对象 | 说明 |
|------|----------|------|
| `Up` / `Down` | 底部 `❯` **输入框** | 回顾已发送的命令/问题（`{state_dir}/cli/history.txt` + 当前会话最近 user 消息，条数上限 `cli.input_history_max`，默认 100） |
| `PageUp` / `PageDown`、滚轮 | 上方 **transcript 输出区** | 滚动已渲染的会话消息；滚到顶可懒加载更早 transcript |
| `Ctrl+L` | transcript 输出区 | 清空并重载最近 transcript；**不会**刷新输入框 ↑↓ 历史 |

全屏 CLI 启动或 `/session switch` 后会**刷新输入框 ↑↓ 历史**（载入新会话的 user 消息）；`Ctrl+L` 不会。启动时 transcript 初始批次使用纯文本快速渲染以加速显示；向上懒加载更早消息时仍可使用 Markdown 渲染。

简易 CLI（无 TUI / fallback）在已安装 `readline`（Windows 可选 `pyreadline3`）时支持 ↑↓ 回顾 `history.txt`。

## 终端 Markdown（Rich，可选）

全屏 CLI 下 Assistant 回复、命令输出与思考过程正文的 Markdown 渲染（含 Rich 安装、`pip install -e ".[cli]"`）见 **[OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)** §1（SSOT）。

## 键盘快捷键

| 按键 | 功能 |
|------|------|
| `Ctrl+C` | 中断当前操作 / 退出程序 |
| `Ctrl+D` | 退出程序（备选方式） |
| `Ctrl+L` | 清屏重绘（清空transcript） |
| `Ctrl+T` | 显示后台任务列表（预览） |
| `PageUp` | 上翻输出区约半屏 |
| `PageDown` | 下翻输出区约半屏 |
| `Ctrl+Home` | 光标跳到输入开头 |
| `Ctrl+End` | 光标跳到输入末尾 |
| `Shift+Left` | 水平向左滚动（仅窄终端时生效） |
| `Shift+Right` | 水平向右滚动（仅窄终端时生效） |
| `Up` | 浏览上一条**输入框**历史（已发送命令/问题；非 transcript 滚动） |
| `Down` | 浏览下一条**输入框**历史 |
| `Tab` | 自动补全命令或文件路径 |
| `Shift+Tab` | 向前循环补全选项 |

**水平滚动说明**：当终端宽度小于 60 列时，自动禁用折行，启用水平滚动。`Shift+Left/Right` 每步滚动约 10 字符。

**退出说明**：Ctrl+C 和 Ctrl+D 都可退出程序，效果相同。Ctrl+C 也可用于中断正在运行的Agent操作。

**Tab 补全说明**：
- 输入 `/` 开头时补全命令（如 `/st` → `/status`）
- 输入 `@file:` 或 `file:` 后补全文件路径
- 按 `Tab` 向后循环选项，`Shift+Tab` 向前循环

## 鼠标交互

| 操作 | 功能 |
|------|------|
| 滚轮向上 | 向上滚动输出区（约视口 1/6） |
| 滚轮向下 | 向下滚动输出区（约视口 1/6） |
| 点击滚动条 | 直接跳转到对应位置 |
| 拖动滚动条 | 平滑垂直滚动 |
| 拖动内容区 | 水平滚动（仅窄终端时生效） |

**滚动条交互**：垂直滚动条位于右侧约 1-2 列。点击可直接跳转，拖动可平滑滚动。

**水平拖动**：当终端宽度小于 60 列时，可在内容区域拖动鼠标进行水平滚动。

## 输入前缀

MiniAgent 支持多种输入前缀，快速触发不同功能：

| 前缀 | 功能 | 示例 |
|------|------|------|
| `/cmd` | CLI命令 | `/help`, `/status` |
| `!cmd` | 直接执行Bash命令 | `!ls -la`, `!git status` |
| `@file:<路径>` | 文件引用 | `@file:image.png` |

### !cmd — Bash直接执行

使用 `!` 前缀可直接执行Bash命令，无需经过Agent处理：

```
> !ls -la
⚠️ Bash执行: ls -la
total 32
drwxr-xr-x  8 user  group  256 Jan 1 10:00 .
...

> !git status
⚠️ Bash执行: git status
On branch main
...
```

**特点**：
- 超时保护（10秒）
- 输出直接显示在transcript
- 不影响Agent上下文
- 错误信息清晰展示

**限制**：复杂交互命令（如vim、top）可能不适用。

### @file — 文件引用

在 CLI 中使用 `@file:<路径>` 或 `file:<路径>` 标记可上传文件：

```
> 请分析 @file:image.png 的内容
📎 已处理文件: image.png (150KB)
   内容摘要: 这是一张展示...
```

**图片视觉描述**：
- 默认启用 `cli.file_vision_desc`（见 `miniagent/resources/config.defaults.json`）
- 图片会调用配置的视觉模型生成描述，并注入到对话历史
- 设为 `0` 可禁用此功能

**Agent 主动分析图片**：
- 除了入站时的被动描述，Agent 还可通过 `analyze_image` 工具主动分析工作区内的图片
- 例如：「请分析 feishu_incoming 目录下刚才上传的截图」
- 支持自定义分析提示词，如「识别图中文字」「描述图中人物动作」

**支持的文件类型**：
- 图片：png、jpg、jpeg、gif、webp、bmp
- 文本文件：自动提取前 200 字符预览
- 二进制文件：仅记录元数据

## 命令总览

| 分类 | 命令 | 说明 |
|------|------|------|
| **会话** | `/session list` | 列出所有会话 |
| | `/session switch <编号/ID>` | 切换会话（含 `oc_xxx` 飞书群聚焦；同步 CLI/自动私聊绑定） |
| | `/session create <ID> [标题]` | 创建新会话 |
| | `/session rename <编号/ID> <新标题>` | 重命名会话 |
| | `/session delete <编号/ID>` | 删除会话 |
| **实例** | `/instance list` | 列出运行实例 |
| | `/instance stop <ID>` | 停止指定实例 |
| **飞书** | `/feishu start` | 启动飞书连接 |
| | `/feishu stop` | 停止飞书连接 |
| | `/feishu status` | 查看飞书状态 |
| **队列** | `/queue status` | 查看消息队列状态 |
| | `/query` | 同上（`/queue status` 短命令） |
| | `/queue set <模式>` | 切换 queue / preemptive |
| | `/queue abort` / `/abort` | 中止本 `chat_id` 上 `dispatch` / `dispatch_wait` 投递的任务（非 `/stop`） |
| **后台任务** | `/btw start <提示词>` | 启动后台任务（子session并行执行） |
| | `/btw status [任务ID]` | 查看任务状态（无ID时显示列表） |
| | `/btw result <任务ID>` | 获取任务结果 |
| | `/btw cancel <任务ID>` | 取消任务 |
| | `/btw clear` | 清理已完成的任务 |
| **定时任务** | `/schedule` | 无参或与 `list` 相同：列出用法与子命令 |
| | `/schedule list` | 列出所有定时任务 |
| | `/schedule show <id>` | 打印任务 JSON |
| | `/schedule add …` | 新增 interval / once / cron 任务（须含 ` -- ` 分隔 prompt） |
| | `/schedule update <id> …` | 修改已有任务（语法同 add） |
| | `/schedule remove|enable|disable <id>` | 删除 / 启用 / 禁用 |
| **知识库** | `/kb list` | 列出已挂载的知识库 |
| | `/kb mount <路径> [名称]` | 挂载知识库（目录或文件） |
| | `/kb unmount <名称>` | 卸载知识库 |
| | `/kb search <关键词> [名称]` | 检索知识库内容 |
| | `/kb reload [名称]` | 重新加载知识库 |
| **确认** | `/confirm` | 确认待处理的确认请求 |
| | `/adjust <内容>` | 调整并确认待处理请求 |
| | `/reject` | 拒绝待处理请求 |
| **答案改进** | `/improve` | 根据质量评估建议改进上一轮答案 |
| | `/improve --force` | 强制改进（即使质量已通过） |
| | `/improve --reset` | 回退到原始答案重新改进 |
| | `/review` | 自我反驳式审查答案（迭代最多3轮） |
| **工具与统计** | `/stats` | 工具调用统计 |
| | `/status` | 查看系统运行状态 |
| **自我优化** | `/self-opt status` | 查看自我优化子系统状态 |
| | `/self-opt proposals [status]` | 列出优化提案 |
| | `/self-opt show <proposal_id>` | 查看提案详情 |
| | `/self-opt approve <proposal_id>` | 批准提案 |
| | `/self-opt reject <proposal_id>` | 拒绝提案 |
| | `/self-opt apply <proposal_id> [root]` | 执行已批准提案 |
| | `/self-opt analyze` | 触发运行分析 |
| | `/self-opt report [date]` | 查看分析报告 |
| **自测命令** | `/test run` | 运行所有测试样本（默认 mock 模式） |
| | `/test run <类别>` | 按类别过滤（security | prompt_injection | tool_selection | schema | regression | cost） |
| | `/test list` | 列出所有测试样本 |
| | `/test status` | 查看最近测试结果 |
| **实例控制** | `/stop` | 停止当前实例并退出 |
| | `/copy` | 复制当前会话全文到剪贴板（全屏 transcript / 简易 history） |
| **技能** | `/reload-skills` | 从磁盘全量重新加载 `workspaces/skills`（`install_skill` 成功后通常已自动热加载） |
| **配置** | `/config [section]` | 查看配置概览；指定 section 时查看该部分（如 model、paths、feishu） |
| | `/model [name]` | 显示当前模型；指定 name 时切换模型 |
| | `/reload-config` | 重新加载 config.user.json（配置热更新） |
| | `/doctor` | 诊断安装与配置 |
| **其他** | `/help` | 显示帮助信息 |
| | `quit` / `exit` | 退出程序 |

## 命令详解

### /status — Agent 状态检查

**不中断执行**，用于怀疑 Agent 卡死时的诊断。

```
> /status

🏭 实例: #1
📁 当前会话: #1 default
💬 飞书: 🟢 运行中

📬 消息队列:
  模式: 🟢 queue
  oc_xxxxx: 🔴 处理中 (45.2s)
    等待: 2 条
  CLI: ⚪ 空闲
```

### /session — 会话管理

所有会话命令同时支持**编号**和**原始 ID**。

```
> /session list

📋 会话列表:
  - #1 default ← 当前 | 5 轮 🔒 (本实例)
  - #2 cli-interactive | 3 轮
  - #3 oc_3a135408 | 1 轮

> /session switch 2
🔄 已切换到会话: #2 cli-interactive

> /session create test-session 测试会话
✅ 已创建会话: #4 测试会话

> /session rename 4 我的测试
✅ 已重命名: #4 我的测试

> /session delete 4
✅ 已删除会话: #4 我的测试
```

### /instance — 多实例管理

```
> /instance list

🏭 运行实例:
  #1  PID=12345  模式=both    启动=12:30  会话=[default]  ← 当前
  #2  PID=12346  模式=cli     启动=12:35  会话=[test]

> /instance stop 2
✅ 实例 #2 已停止
```

`模式` 列：`cli` 为仅 CLI；`both` 为 CLI + 飞书连接已启用。产品仅这两种启动形态，不存在无 CLI 的独立飞书进程入口。

### /queue — 消息队列

```
> /queue status

📬 消息队列状态
  模式: 🟢 队列模式 (queue)
  oc_xxxxx: 空闲
  CLI: 空闲

> /queue set preemptive
✅ 已切换到 打断模式（最新消息打断前面处理）
```

**中止队列：**`/queue abort` 与短命令 `/abort` 会取消当前聊天室（飞书为当前群/私聊 `chat_id`，CLI 为内部 `__cli__`）上排队与执行中的任务，包括经 `dispatch_wait` 投递的回合（如部分定时任务）。不会退出进程；与 `/stop`（停实例）不同。

**两种模式：**
- `queue`（默认）：消息按顺序处理
- `preemptive`：最新消息打断当前处理

### /btw — 后台任务

在主 session 中启动子 session 并行执行任务，不污染主对话历史。

**核心特性**：
- **独立 session_key**：后台任务使用 `__bg__<uuid>` 作为 session_key，完全隔离
- **并行上限**：最多同时运行 4 个后台任务
- **异步执行**：启动后立即返回任务 ID，可继续主对话
- **结果查询**：任务完成后可随时获取结果

**子命令**：

| 命令 | 说明 |
|------|------|
| `/btw start <提示词>` | 启动后台任务，返回任务 ID |
| `/btw status [任务ID]` | 无 ID 时显示任务列表；有 ID 时显示详细状态 |
| `/btw result <任务ID>` | 获取任务执行结果（等待最多 30 秒） |
| `/btw cancel <任务ID>` | 取消待执行或运行中的任务 |
| `/btw clear` | 清理已完成/失败/取消的任务 |

**使用示例**：

```
> /btw start 帮我分析 workspace 目录下的所有 Python 文件的依赖关系
✅ 后台任务已启动: a1b2c3d4
   输入: 帮我分析 workspace 目录下的所有 Python 文件的依赖关系...
   使用 /btw status a1b2c3d4 查看进度

> /btw status

## 后台任务列表

⏳ **a1b2c3d4**: pending - 帮我分析 workspace 目录下的所有...
🔄 **e5f6g7h8**: running - 检查代码覆盖率...
✅ **i9j0k1l2**: completed - 生成 API 文档

统计: 3 个任务
并行上限: 4，当前运行: 1

> /btw result i9j0k1l2

## 任务 i9j0k1l2 结果

（任务执行结果内容）

> /btw clear
✅ 已清理 1 个已完成任务
```

**任务状态**：
- `pending` ⏳：等待执行
- `running` 🔄：正在执行
- `completed` ✅：已完成
- `failed` ❌：执行失败
- `cancelled` 🚫：已取消

**键盘快捷键**：`Ctrl+T` 显示后台任务列表预览。

### /schedule — 定时任务

任务持久化在 **`{paths.state_dir}/scheduled_tasks/tasks.json`**（canonical 布局见 [ENGINEERING.md](ENGINEERING.md) §3）。触发时与手动输入一样经 **消息队列** 跑一轮 Agent。详见 [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」与 [USER_GUIDE.md](USER_GUIDE.md) §3。

**语法摘要**（与无参 `/schedule` 打印一致）：

```
/schedule list
/schedule show <id>
/schedule remove <id>
/schedule enable <id>   |   /schedule disable <id>
/schedule add <id> every <秒> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
/schedule add <id> once <ISO8601> <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
/schedule add <id> cron "<分> <时> <日> <月> <周>" <primary|ephemeral|fixed:会话ID> [--tz IANA] -- <prompt>
```

**要点**：

- **`add` 必须包含 ` -- `**（空格、两个连字符、空格）：前面为调度与会话参数，后面为交给模型的 **prompt**；缺少分隔符会报错。
- **`every`**：间隔秒数为正整数；**`once`**：时间为 ISO8601（可含 `Z` 或 `+08:00`）；未带时区的 naive 时间由 **`--tz`** 解释（未写时读 `scheduled_tasks.timezone` → `timezone.default` → `TZ`）。
- **飞书收结果**：飞书 WebSocket 已连接且任务为 **`primary`** 且已与飞书私聊绑定时，定时任务会镜像思考流与最终回复到飞书（`scheduled_tasks.feishu_mirror=false` 关闭）；详见 [USER_GUIDE.md](USER_GUIDE.md) §3。
- **会话**：`primary` 使用当前路由的主会话 / 活跃会话；`ephemeral` 每次新建临时会话键；`fixed:会话ID` 固定到某会话（如 `fixed:default` 或 `fixed:feishu:oc_xxx`，后者可用于飞书群任务）。
- **时区**：cron 墙钟以 `tasks.json` 内 `schedule.timezone` 为准；未写 `--tz` 时新建任务默认时区为 `scheduled_tasks.timezone` → `timezone.default` → `TZ` → `Asia/Shanghai`。
- **关闭调度循环**（不删任务表）：`scheduled_tasks.disabled=true`；dispatch 失败退避秒数：`scheduled_tasks.dispatch_backoff`（默认 60，见包内 defaults）。

**飞书渠道**：在飞书里发 `/schedule` 时，通常 **仅允许** `list` / `show`；`add` / `remove` / `enable` / `disable` 须在 **本机 CLI** 执行（与 `/session` 变异限制类似）。

**Agent 工具**（可选）：`run_dot_command` 由 `cli.dot_tools_enabled` 控制；`manage_scheduled_task` 由 `scheduled_tools.enabled` 控制。见包内 defaults。

### 通道路由（无 `/bind` 命令）

CLI 与飞书私聊的会话映射由运行时自动维护；查看绑定与聚焦模式用 **`/status`**。完整规则、场景与诊断见 **[FEISHU.md §通道绑定](FEISHU.md#通道绑定)**（SSOT）。

### /feishu — 飞书控制

```
> /feishu start
[飞书] 正在启动 WebSocket 长轮询...
✅ 飞书已启动

> /feishu status
🟢 飞书: 运行中

> /feishu stop
✅ 飞书已停止
```

### /kb — 知识库管理

挂载本地文档供 Agent 检索。知识库目录应有 `KB.yaml` 或 `files/` 子目录。

```
> /kb list

📚 已挂载知识库:
  - project_docs: 12 条目, 150 关键词
    路径: /path/to/project_docs

> /kb mount /path/to/docs my_kb
✅ 已挂载知识库: my_kb
   条目数: 5, 关键词: 80

> /kb search API 接口 my_kb
## 知识库: my_kb

### api.md
本文档描述 API 接口规范...

> /kb unmount my_kb
✅ 已卸载知识库: my_kb

> /kb reload my_kb
✅ 已重载知识库: my_kb
```

**Agent 工具**：`search_knowledge`、`read_knowledge_file`、`kb_list`。

目录结构与 `KB.yaml` 格式见 **[KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)**（SSOT）。

### /review — 答案审查

对当前会话中的最后一条回复进行「自我反驳式」审查，发现逻辑漏洞、事实错误或遗漏，并输出改进建议。需要会话上下文和会话管理器。

```
> /review

🔍 审查结果: 发现 2 个问题
1. 未考虑时区差异，计算结果可能偏差
2. 数据来源未标注，可信度无法评估

💡 改进答案: ...
```

### /improve — 答案改进

根据质量评估建议改进上一轮答案，支持多轮改进。

```
> /improve
🔄 正在根据建议改进答案…
✅ 答案已改进

> /improve --force
强制改进（即使质量评估已通过）

> /improve --reset
回退到原始答案，重新开始改进流程
```

### /test — 自测命令

运行内置测试样本验证 Agent 行为，默认使用 mock 模式（不调用真实 LLM）。

```
> /test run
🧪 正在运行自测...
🧪 自测结果：5/5 通过 (100.0%)

> /test run security
按类别过滤测试

> /test list
列出所有测试样本

> /test status
查看最近一次测试报告
```

测试样本位于 `tests/evaluation/samples/`。

### /self-opt — 自我优化

基于 Trace 运行指标与代码静态分析生成优化提案。**配置、子命令详解、工作流与 API** 见 **[SELF_OPT.md](SELF_OPT.md)**（SSOT）。

```
> /self-opt status
> /self-opt proposals pending
> /self-opt analyze
> /self-opt show <proposal_id>
> /self-opt approve <proposal_id>
> /self-opt apply <proposal_id>
```

### /reload-config — 配置热更新

重新加载 `config.user.json`，使配置修改立即生效无需重启：

```
> /reload-config
✅ 配置已重新加载
```

**自动热更新**：设置 `features.config_hot_reload=true` 后，修改配置文件会自动触发重载（每5秒检查，2秒防抖）。

**配置监听**：
- 监听文件：项目根目录的 `config.user.json`
- 检查间隔：5秒
- 防抖延迟：2秒（避免部分写入）

### /confirm / /adjust / /reject — 确认侧通道

当 Agent 通过确认通道（`ConfirmationChannel`）发起待确认请求时，可用以下命令响应：

```
> /confirm
✅ 已确认通过

> /adjust 使用北京时间计算
✅ 已调整并确认：使用北京时间计算

> /reject
✅ 已拒绝
```

`/confirm` 直接通过；`/adjust` 携带调整内容作为回答注入；`/reject` 拒绝请求。

## 用户体验增强

MiniAgent 提供多项用户体验增强功能，提升交互效率与稳定性。

### 思考过程颜色

CLI 思考块标题与正文默认使用亮青色（`ansibrightcyan`）。颜色为 **Internal 常量**，定义在 `miniagent/core/constants.py`（`CLI_STYLE_THINK_HEAD` / `CLI_STYLE_THINK_BODY`）；**暂不支持** `config.user.json` 或环境变量覆盖。定制需修改源码常量；若未来实现 `cli.styles` 配置项，将在此文档化。

### 命令模糊匹配

输入 `/` 命令时自动检测错别字并提供建议：

```
> /sttatus
⚠️ 未找到命令 '/sttatus'，您是否想输入 '/status'？

> /hlep
⚠️ 未找到命令 '/hlep'，您是否想输入 '/help'？

> /sesion list
⚠️ 未找到命令 '/sesion'，您是否想输入 '/session'？
```

**匹配策略**：
- **前缀匹配**：输入至少3字符时优先匹配前缀（如 `/sta` → `/stats`）
- **模糊匹配**：相似度阈值 0.6，使用 `difflib.get_close_matches()`
- **建议而非自动执行**：用户需手动确认后重新输入

### Tab 自动补全

按 `Tab` 键自动补全命令和文件路径：

**命令补全**：
```
输入 /st + Tab → 显示选项：
  /stats   /status   /stop   /instance
```

**文件路径补全**：
```
输入 @file:D:/AI + Tab → 显示匹配路径：
  D:/AIhub/   D:/AIProjects/
```

**操作方式**：
- `Tab`：向后循环选项
- `Shift+Tab`：向前循环选项
- 选择后自动插入完整内容

### 网络连接可靠性

HTTP 请求自动重试，提升网络稳定性：

**重试策略**：
- **5xx 错误**：重试（服务器错误）
- **4xx 错误**：不重试（客户端错误）
- **网络错误**：重试（连接失败）

**参数配置**（实现层，**不是** `config.user.json` 用户键名）：
- 通用 HTTP 客户端（`infrastructure/http_retry.py`）：函数参数 `max_retries` 默认 3；`backoff_factor` 默认 1.0（间隔：1s → 2s → 4s）
- 模型客户端：用户 JSON 使用 **`model.retry_count`**（默认见 `config.defaults.json`）；OpenAI SDK 内部另有原生 `max_retries`（常见为 2），勿与 JSON 键混淆
- `agent.http_timeout`：默认 120 秒（包内 defaults，**这是**合法 JSON 键）

**覆盖模块**：
- OpenAI API 调用（配置键 `model.retry_count`；SDK 另有原生重试）
- Embedding 搜索
- ClawHub 客户端
- 飞书 Drive API

### 多会话并行（Agent）

| JSON 路径 | 默认 | 说明 |
|-----------|------|------|
| `agent.parallel_sessions` | `true` | 不同 `session_key` 可并行跑 Agent；`false` 时全局串行（旧行为） |
| `agent.max_parallel_sessions` | `4` | 进程内同时运行的 Agent 会话上限（含飞书多群与 `/btw` 后台任务） |

同一飞书群内消息仍按 `chat_id` 队列顺序处理；不同群在默认配置下可同时执行。

### 配置热更新

配置文件修改后立即生效，无需重启：

**手动触发**：
```
> /reload-config
✅ 配置已重新加载
```

**自动监听**（需启用）：
1. 设置 `features.config_hot_reload=true`
2. 监听 `config.user.json` 文件修改
3. 每 5 秒检查文件 mtime
4. 检测到修改后等待 2 秒（防抖）
5. 自动调用 `reload_config()`

**首次配置引导**：
- 首次运行时检测无 `config.user.json`
- 交互式提示配置 API 密钥、模型、端点
- 自动生成配置文件

## 飞书中使用命令

飞书消息以 `/` 开头时，自动路由到命令调度器而非 Agent：

- **多数**命令（如 `/status`、`/help`、`/queue status`）可在飞书使用。
- **默认仅本机 CLI**：`/schedule` 的 `add` / `update` / `remove` / `enable` / `disable`；`/session` 的 `switch` / `create` / `rename`；`/stop`（与 [USER_GUIDE.md](USER_GUIDE.md) 第 2、3 章一致）。
- **全开**：设置 `feishu.dot_commands_full=true` 后飞书与 CLI 命令能力相同（见 [FEISHU.md](FEISHU.md)）。

```
飞书发送: /status
飞书回复: 🏭 实例: #1 ...

飞书发送: /help
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

- [ENGINEERING.md](ENGINEERING.md)：本地与 CI 质量门禁、`paths.state_dir`。
- [SECURITY.md](SECURITY.md)：沙箱与工具安全模型。
- [FEISHU.md](FEISHU.md) §通道绑定：CLI 与飞书会话绑定。
- [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)：知识库挂载与检索。
- [SELF_OPT.md](SELF_OPT.md)：`/self-opt` 命令、提案工作流与配置。
