# 飞书集成文档

> Mini Agent Python | 版本: 2.0.2 | 飞书 WebSocket 长连接

## 快速开始

### 1. 配置环境变量

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_VERIFICATION_TOKEN="xxx"
```

或在 `.env` 文件中配置。

### 2. 启动

```bash
python -m miniagent --feishu
```

或在 CLI 中运行：`.feishu start`

**启动形态**：进程始终以 **CLI 主循环** 为主；上述两种方式均为 **CLI + 飞书**（同进程内附加飞书 WebSocket 长连接），不存在无 CLI 的独立飞书进程入口。

在全屏 prompt_toolkit CLI 下，飞书启动提示、入站横幅、以及与 CLI 绑定同一会话时的「思考」镜像，都会写入上方 **transcript**（`RuntimeContext.cli_transcript_append`），而不再向裸 stdout `print`，避免与备用屏输入行互相覆盖。

`get_logger()` 的诊断输出写入 **stderr**（不再写 stdout）；飞书 WebSocket 客户端 SDK 日志级别为 **ERROR**，避免与全屏 UI 争用终端。

全屏 CLI 运行时会暂时把 ``get_logger`` 控制台输出提高到 **WARNING**（集成终端里 stderr 仍会打乱备用屏）。调试若需要 INFO/DEBUG，可设置环境变量 **`MINI_AGENT_TUI_VERBOSE_LOG=1`**。

在飞书里发送以 ``.`` 开头的命令时，默认 `.session switch` / `create` / `rename` 以及 `.schedule` 的 `add`/`update`/`remove`/`enable`/`disable`/`align-tz` **不会**修改与本地 CLI 共享的 ``active_session_id`` 或 ``tasks.json``，仅返回提示；``.stop`` 亦默认拒绝（避免远程结束进程）。请在本地 MiniAgent 终端执行，或设置 **`MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1`** 放开全部点命令（启动时会打 WARNING；群聊误触风险需自行管控）。启用 FULL 后飞书侧 `.stop` 成功即进程退出，通常**不会**再收到第二条飞书确认消息。调试 HTTP 栈时请勿开启 ``HTTPX_LOG_LEVEL=debug`` 等会把第三方日志打到终端的配置，以免干扰全屏 UI。

Agent 在飞书会话中若通过内置工具 **`run_dot_command`** 调点命令，上述限制与直接发点命令一致（默认 `cli_dispatch_allow_mutations=False`；`MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1` 时为 True）。不需要该能力时可将 **`MINIAGENT_CLI_DOT_TOOLS=0`**，启动时不再注册该工具（见仓库根目录 `.env.example`）。

## 运维速查（WebSocket）

连接由 `poll_server` + `ws_health` 监督；默认关闭 SDK 内建 `auto_reconnect`，断线后由 `FeishuRuntime` 外层退避重建。Windows 上 `WinError 121`、收包循环退出等日志后若出现「约 Xs 后重连」，属**预期自愈**。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `MINIAGENT_FEISHU_WS_AUTO_RECONNECT` | `0` | `1` 启用 SDK 内建重连（不推荐） |
| `MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S` | `30` | 看门狗轮询（秒）；**非**多实例注册心跳 |
| `MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S` | `90` | 连接为空超过该秒数则重建 |
| `MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S` | `300` | 仅 `AUTO_RECONNECT=1` 时生效 |
| `MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S` | `0` | 定期主动刷新；不稳定网络可设 `3600` |
| `MINIAGENT_FEISHU_WS_IDLE_REFRESH_S` | `0` | 无入站超过 N 秒刷新（默认关） |

私聊绑定与排障见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)；Windows 专项见下文「Windows / 长连接」。

## 架构

```
飞书开放平台
    │ WebSocket 长连接
    ▼
miniagent/feishu/poll_server.py
    │
    ▼
engine.main._create_feishu_handler() → (text_handler, media_handler)
    │                                      （text 以 ``FeishuInboundText`` 调 Agent；file/image 走 media_handler）
    ▼
ChannelRouter.resolve_feishu_message(chat_id, sender_id, chat_type)
    │
    ├── 群聊: 返回 "feishu:<chat_id>" → 独立会话
    └── 私聊: 未绑定时自动绑到当前 CLI 活跃会话后再 resolve
    │
    ▼
UnifiedEngine.run_agent_with_thinking()
    │
    ├── CLI: 终端流式打印思考过程
    └── 飞书（群聊与私聊）: 每轮 LLM 思考 **一条交互卡片**（流式 PATCH 节流；`finalize` 时若超长则 **首张 PATCH + 后续多张「思考中 (k/n)」续页**）；同轮工具意图默认 **追加到该卡片**（与 CLI 的 `MINIAGENT_THINKING_MERGE_TOOLS` 一致；设为 `0` 时工具行仍各建一条短卡片）。最终回复按 `MINI_AGENT_FEISHU_CARD_BODY_MAX`（默认约 48k 字符）**分片多张卡片**；任一分片发送失败则 **中止后续分片**，已发部分不再用整条 `text` 重复回退；仅当交互消息 **一条都未成功** 时才按同上限 **分条 text** 回退全文。
```

### 与会话历史相关的环境变量

| 变量 | 含义 |
|------|------|
| `MINI_AGENT_FEISHU_CARD_BODY_MAX` | 单张交互卡片正文近似上限；过低易增加分片条数。流式思考 PATCH 与每次 `append_feishu_thinking_same_card` 会对**当前累积正文整体**做规范化并可能截断为 `…`；单卡极长时较早内容可能不再显示（完整文本仍在会话 **history.json**） |
| `MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS` | 仅影响 `conversation_history_for_llm()` 对 `thinking` 的映射，不影响飞书 |
| `MINIAGENT_FEISHU_MARKDOWN_COMMANDS` | `1` 时飞书侧 `.session list` / `.queue status` / `.instance list` 使用 Markdown 表格（与 `.help` 同为 lark_md 子集；默认 `0`） |
| `MINIAGENT_FEISHU_DOT_COMMANDS_FULL` | `1`/`true`/`yes`/`on` 时飞书点命令与 CLI 同等（含会话/定时任务变异与 `.stop`；默认 `0`） |
| `MINIAGENT_FEISHU_TABLE_FALLBACK` | 列数超过 `MINIAGENT_FEISHU_LARK_TABLE_MAX_PIPES` 时：`both`（默认）= 提示 + 代码块内等宽文本表；`unicode` = 仅文本表；`hint` = 仅提示 |
| `MINIAGENT_TOOL_INTENT_IN_THINKING` | `0`/`false` 关闭工具执行前的 🔧 意图行（仍保留工具结果全文块） |
| `MINIAGENT_CLI_DOT_TOOLS` | 默认 `1`；`0`/`false`/`off` 时不注册 `run_dot_command`（Agent 无法经工具调点命令） |
| `MINIAGENT_FEISHU_REPLY_PLAIN` | 默认 **开**（`0`/`false`/`off` 关闭）：仅影响**最终 Assistant 回复**，分片前弱化部分 Markdown；**仍为 `interactive` + `lark_md`**；无法识别的非空取值视为 **关** |
| `MINIAGENT_FEISHU_REPLY_TARGET` | 默认 **`reply`**（回复入站 `message_id`）；`create` 为会话内新建消息；非法取值按 `create` 处理 |
| `MINIAGENT_FEISHU_REPLY_IN_THREAD` | 与上一项 `reply` 联用；显式 `1`/`true` 等为话题内回复；`0`/`false` 等为否；**未设置**且入站 `thread_id` 非空时，默认 `reply_in_thread=True`（仍可由显式 `0` 关闭） |
| `MINIAGENT_FEISHU_CARD_ACTION_ROUTER` | 默认 **开**；注册 `p2.card.action.trigger`：从按钮 `action.value` 读取 `miniagent_text`/`text` 与 `chat_id` 后投递同一 `message_handler` 队列；无法识别的非空取值视为 **关** |
| `MINIAGENT_FEISHU_TOOLS` | 真值时注册内置飞书工具；若变量**已设置**但取值既非认可真值也非认可假值，按**关闭**处理（不落入 AUTO） |
| `MINIAGENT_FEISHU_TOOLS_AUTO` | 默认 **开**：未设置 `MINIAGENT_FEISHU_TOOLS` 且已配置 `FEISHU_APP_ID`/`SECRET` 时自动注册上述工具（进程 init，不等待 WebSocket）。显式 `MINIAGENT_FEISHU_TOOLS=0` 可关闭 |
| `MINIAGENT_FEISHU_DOCX_URL_PREFIX` | 创建云文档成功时在输出中附带可分享链接（租户 Web 前缀，如 `https://your-tenant.feishu.cn/docx`） |
| `MINIAGENT_FEISHU_RECEIVE_ID_TYPE` | `create` 发消息时的 `receive_id_type`：`chat_id`（默认）/`open_id`/`union_id`；非 `chat_id` 时内置工具默认 `receive_id` 为入站 **sender_id**（经 `AgentConfig.feishu_im_receive_id` 注入）；可被工具参数或 `AgentConfig.feishu_im_receive_id_type` 覆盖 |
| `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` | 创建/列举云盘时若工具未传 `folder_token`（或无法从 URL 解析），回退使用该云盘文件夹 token |
| `FEISHU_DOC_FOLDER_FALLBACK_ROOT_META` | 默认 **开**；在上述仍无父目录 token 时调用根文件夹元数据 API（`0`/`false` 关闭；需云盘权限） |

### 出站能力矩阵（摘要）

| 能力 | 实现要点 |
|------|----------|
| 会话内新消息 / 回复某条消息 | `create` 与 `im/v1/messages`；`reply` 与 `im/v1/messages/:id/reply`（见上表环境变量） |
| 上传并发 file/image 消息 | `miniagent/feishu/upload_io.py` + 工具 `feishu_send_workspace_file`（需 `MINIAGENT_FEISHU_TOOLS=1` 或 `MINIAGENT_FEISHU_TOOLS_AUTO=1` 与 `FEISHU_APP_ID`/`SECRET`） |
| 云文档创建与 Markdown 读取 | `miniagent/feishu/docx_client.py` + 工具 `feishu_create_document` / `feishu_get_document_markdown`（需开放平台 **docx** 相关权限）；可选 URL 前缀见上表 |
| 云文档末尾追加纯文本 | 工具 `feishu_append_document_text` + [`docx_blocks`](../miniagent/feishu/docx_blocks.py)：调用开放平台 **`document_block_children.create`** 在页面块下插入文本子块（**不是** `document_block.batch_update` / PATCH 通用块编辑） |
| 云盘列举 | 工具 `feishu_list_drive_files` + [`drive_client`](../miniagent/feishu/drive_client.py)（需云盘读权限）；`folder_token` 可为文件夹 token、云盘分享链接中的路径 token，或与创建文档相同的环境默认 / 可选根目录回退 |
| 通用块级 `document_block.batch_update` / 富文本块编辑 | **未封装**；若需改已有块或复杂排版，须另行对接开放平台 API |
| 撤回机器人消息 | 工具 `feishu_recall_message`（`im/v1/messages/:id` DELETE） |

模块 [`poll_server`](../miniagent/feishu/poll_server.py) 中的 `FEISHU_CARD_BODY_MAX` 仅为 **首次 import 时的快照**；运行时应使用 `feishu_card_body_max()` 读取当前环境。

### 开放平台权限（scope）

下列为能力对应的**典型**权限名称；具体以飞书开放平台当前文档为准（[权限列表](https://open.feishu.cn/document/server-docs/docs/scope)）。

| 能力 | 说明 |
|------|------|
| IM 发消息 / 回复 | 机器人收发消息相关能力（如 `im:message` 等，依应用类型与订阅事件而定） |
| 上传图片 / 文件并发 IM | 消息内资源与上传相关能力 |
| 删除消息 | 与 `im/v1/messages/:message_id` 删除接口对应的权限 |
| 云文档创建与读 raw_content | docx 文档读写相关能力 |
| 云盘列举文件夹 | drive 文件列表相关能力（以当前开放平台文档为准） |
| 卡片交互回调 | 需在开放平台配置事件订阅（如 `p2.card.action.trigger`）与卡片 `action` 行为 |

### 飞书工具与 IM 自检清单

发文件/建文档失败时，工具返回里会带开放平台 **`code` / `msg`**（及常见 `log_id`），请按下列顺序排查：

1. **依赖**：已 `pip install -e ".[feishu]"`（或 `miniagent-python[feishu]`），进程能 `import lark_oapi`。
2. **工具开关**：`MINIAGENT_FEISHU_TOOLS=1`，或 **未设置** `MINIAGENT_FEISHU_TOOLS` 且默认已开的 `MINIAGENT_FEISHU_TOOLS_AUTO`（需 App ID/Secret）。显式 `MINIAGENT_FEISHU_TOOLS=0` 或 `MINIAGENT_FEISHU_TOOLS_AUTO=0` 可关闭。凭证齐全但未注册扩展工具时，进程会打 **INFO** 自检日志指向本节。
3. **凭证**：`FEISHU_APP_ID`、`FEISHU_APP_SECRET` 已配置（与 WebSocket 长连接使用同一应用）。
4. **权限**：应用已开通上表 IM / docx / drive 等能力，机器人已进群（群聊），租户未禁用量级调用。
5. **会话 ID**：默认 `receive_id_type=chat_id`，工具使用当前回合的 **群/会话 `chat_id`**。若设置 `MINIAGENT_FEISHU_RECEIVE_ID_TYPE=open_id`（或 `union_id`），须与 **`receive_id` 同类型**；此时默认 `receive_id` 为入站注入的 **发送者 `sender_id`（通常为 open_id）**，缺省则须在工具参数中显式传入 `receive_id`。
6. **云文档目录**：`feishu_create_document` / `feishu_list_drive_files` 需要云盘**父文件夹** `folder_token`；可直接传 token，或传飞书云盘**文件夹分享链接**（工具会解析路径中的 `folder/<token>`）；也可配置 `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN`。若仍无 token，默认会尝试根目录元数据 API（`FEISHU_DOC_FOLDER_FALLBACK_ROOT_META`，默认开；`0` 关闭）。仍失败时按工具返回的「已尝试」项排查。
7. **权威说明**：[飞书权限列表](https://open.feishu.cn/document/server-docs/docs/scope) 以开放平台当前文档为准。

### 飞书与会话工作区文件（发附件）

内置工具 `feishu_send_workspace_file` 的 `relative_path` 必须是**相对当前会话工作区根**的路径（与 `SessionManager` 为该会话分配的 `files_path` 一致，通常为 `…/sessions/<safe_id>/files/`）。飞书用户发到机器人的 file/image 经入站 `media_handler` 保存到 **`files/feishu_incoming/`** 下，发送时应使用该相对路径（例如 `files/feishu_incoming/报告_msgid.pdf`）。

**不是**用户操作系统上的任意绝对路径。若需发送尚未在工作区内的内容，应先用会话内的文件读写工具写入 `files/` 后再调用发送工具。

## chat_type 与入站结构体

生产路径中文本入站使用单参 **`FeishuInboundText`**（定义见 [`miniagent/feishu/types.py`](../miniagent/feishu/types.py)），其中字段 **`chat_type`** 区分群聊与私聊；不再向 handler 单独传入 `chat_type` 位置参数。

| chat_type | 行为 | session_key |
|-----------|------|------------|
| `group` | 独立会话，始终创建/使用 `feishu:<chat_id>` | `feishu:<chat_id>` |
| `p2p` | 若尚未绑定，则自动 `bind(feishu_p2p:<sender>, active_session_id)`；已绑定则使用目标 session_key | 通常为当前 CLI 活跃会话 |

**飞书入站独占**：同一 `MINI_AGENT_STATE` 下通过 `workspaces/feishu_inbound_owner.json`（或 `MINI_AGENT_STATE` 根目录下的 `feishu_inbound_owner.json`）保证**仅一个存活进程**可成功执行 `.feishu start` 并持有常驻重连任务。避免多开实例重复收消息。

**常驻与锁**：`FeishuRuntime` 在后台任务中循环调用 `start_feishu_poll_server`；单次 WebSocket 断线或启动失败会**指数退避后自动重连**，此期间**不释放入站锁**（其它进程仍无法抢占）。仅在执行 `.feishu stop`、任务被取消或进程退出路径上释放锁。

**WebSocket 会话监督**（`miniagent/feishu/ws_health.py`）：连接成功后由看门狗监督收包任务与连接状态；收包循环退出、连接长时间为空、或达到定期刷新间隔时，会结束当前会话并由 `FeishuRuntime` 外层退避重建。默认**关闭** SDK 内建 `auto_reconnect`，避免与外层重连脱节导致「进程显示运行中但收不到消息」。`.feishu status` 可查看上次会话结束原因与最后入站时间。

### Windows / 长连接

Windows 上可能出现 `OSError: [WinError 121]` 或日志 `receive message loop exit` / `no close frame received or sent`（网络休眠、VPN、NAT、网卡节能等）。增强后这些日志后通常会紧跟 `飞书 WS 会话监督结束` 与 CLI `约 Xs 后重连`，属**预期自愈**，不等于进程崩溃。WebSocket 环境变量表见上文 [运维速查（WebSocket）](#运维速查websocket)。

运维建议：电源计划中避免网卡「允许计算机关闭此设备以节约电源」；不稳定网络可设 `MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S=3600`。

**私聊绑定**：手动 `.bind feishu` 仍可用；自动绑定的 sender 会随 `.session switch` 与 CLI 一起切会话，手动绑定过的 sender 不再参与自动重绑。

## 消息处理流程

### 文本（`message_type == text`）

1. **接收消息** — WebSocket 长连接接收事件
2. **提取内容** — 解析 `chat_id`、`sender_id`、`chat_type`、消息文本，以及开放平台事件中的 **`message_id`**、**`root_id`**、**`parent_id`**、**`thread_id`**（后三者可能为空；用于话题上下文与 `MINIAGENT_FEISHU_REPLY_TARGET=reply` 时的默认话题内回复策略）
3. **命令拦截** — 以 `.` 开头的消息路由到 `dispatch_command()`（默认拒绝会话/定时任务变异与 `.stop`；``MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1`` 时与 CLI 同等）。例如 `.help` 与可选 ``MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1`` 下的 `.session list` 等返回 Markdown **表格**，依赖客户端对 GFM / `lark_md` 子集的支持；若表格显示异常可改用本地 CLI 或关闭该变量
4. **解析 session_key** — 通过 `ChannelRouter.resolve_feishu_message()`
5. **运行 Agent** — `run_agent_with_thinking(session_key, ...)`
6. **发送思考** — 与 CLI 一致由 `ThinkingDisplay` 驱动：`push_feishu_thinking_stream()` 对同一逻辑段 PATCH 节流更新；规划阶段为单 header ``[评估与计划]`` 的流式卡；执行阶段为 ``[执行]`` 或分步时的 ``[步骤 i/n] …``；同段内工具结果走 `append_feishu_thinking_same_card()`；阶段切换时仅 `finalize_feishu_thinking_stream()` 收尾当前卡（不另发空思考卡）；**finalize 与 PATCH 共用 `_prepare_thinking_body_for_card`**（折叠空行与 `lark_md` 规范化一致，正文顶格无额外段首/列表缩进）。非流式结论块仍为 `finalize` + `_send_thinking()`。详见 `miniagent/feishu/poll_server.py` 顶部常量（PATCH 频率与单条可 PATCH 次数上限）
7. **发送回复** — `_send_reply()` 使用与思考相同的交互卡片构建（`_feishu_interactive_card_dict` + `_prepare_card_markdown`）；入站 `chat_id` 会先 `_normalize_im_receive_chat_id`，仅当规范化后以 `oc_`（群）或 `ou_`（用户）开头时才发送。

若 `message_handler` 抛错或 `_send_reply` 失败，**不会**把该 `message_id` 写入磁盘去重，便于同一事件在可恢复场景下再次处理。

### 模型输出与 lark_md

飞书交互卡片正文为 **`lark_md` 子集**，并非完整 GFM。发送前会经 `_normalize_lark_md()` 做保守处理（零宽字符、`<br>`、过长围栏、**列数过多**的 Markdown 表格等）。为获得最佳展示，建议模型：

- 优先使用**短列表**与**三级以内标题**，避免过宽表格；若必须表格，控制列数（可用环境变量 `MINIAGENT_FEISHU_LARK_TABLE_MAX_PIPES` 调整阈值，默认按管道符数量判断）。超阈值时由 `MINIAGENT_FEISHU_TABLE_FALLBACK` 决定是否在提示下附带**代码块内的等宽文本表**（便于在客户端内阅读）。
- 代码块使用标准 **三个反引号** 围栏。
- 单独成行的 `---` / `***` / `___`（GFM 分隔线）会替换为横线字符，便于在不支持 ``hr`` 的客户端内阅读。
- 若交互卡片发送失败而回退为 **`msg_type=text`**，飞书客户端**不会**把正文当 Markdown 渲染（纯文本展示），属平台能力限制而非模型未使用 Markdown。日志中会 **WARNING** `飞书发送 msg_type=text 回退（无 lark_md 渲染）: reason=…` 便于区分「渲染降级」与「API 失败」。

### 文件与图片（`file` / `image`）

- 经同一 `chat_id` 消息队列串行调用 `media_handler`：按与文本相同的规则解析 `session_key`、私聊自动绑定，调用开放平台 **获取消息中的资源文件** 接口下载后，写入该会话工作区下的 **`feishu_incoming/`** 目录（与 `SessionManager` 中会话的 `files_path` 一致，即 `workspaces/sessions/<safe_id>/files/feishu_incoming/`）。
- 下载或处理失败（`media_handler` 返回以「⚠️」开头的提示）时，**不写入磁盘去重**，避免永久跳过。
- **`MINIAGENT_FEISHU_MEDIA_RUN_AGENT`**：设为 `1` / `true` / `yes` / `on` 时，在成功落盘后追加一条合成用户消息并调用 `run_agent_with_thinking`。
- **`MINIAGENT_FEISHU_MEDIA_SILENT_REPLY`**：同上真值时，落盘成功仍**不向飞书发送** `_send_reply`（CLI 镜像日志不受影响）。
- 富文本 **`post`**：对 `content` JSON 递归收集 ``tag==img``（`image_key` / `image_token`）与 ``tag==media``（`file_key`），按顺序逐条调用 `media_handler`；**任一条失败**则整消息不入磁盘去重（与单条 file/image 一致）。`file` 消息的 `content` 同时兼容 `file_name` 与 `name` 字段。

## 关键修复

### receive_id 有效性

飞书 API 要求 `receive_id` 必须以 `oc_` 开头（群聊）或 `ou_` 开头（用户）。

```python
# ✅ 正确：使用真实 chat_id
reply = await engine.run_agent_with_thinking(content, chat_id, ...)

# ❌ 错误：使用了 session_id（如 "default"）
reply = await engine.run_agent_with_thinking(content, active_session_id, ...)
```

### 会话隔离

- **群聊**（`chat_type=group`）：每个 `chat_id` 使用独立会话 `feishu:<chat_id>`。
- **私聊**（`p2p`）：未绑定时可自动绑到 CLI 活跃会话，与 CLI **共享**同一 `session_key`；已手动绑定或 `.session switch` 后行为见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

### chat_type 支持（私聊 vs 群聊）

系统通过 **`FeishuInboundText.chat_type`**（或与媒体路径等价的元数据）区分飞书消息类型：

| chat_type | 会话策略 | session_key 格式 | 参与绑定 |
|-----------|----------|------------------|----------|
| `group` | 始终独立会话 | `feishu:<chat_id>` | 否 |
| `p2p` | 检查绑定映射 | `feishu_p2p:<sender_id>` | 是 |

- **群聊**：每个群自动创建独立会话，多群完全隔离
- **私聊**：默认独立，可通过 `.bind feishu <sender_id> <会话>` 绑定到其他会话
- **绑定效果**：私聊消息使用绑定会话的上下文；CLI 终端实时打印预览

**Agent 配置字段**：`AgentConfig` 中的 **`feishu_root_id`** / **`feishu_parent_id`** / **`feishu_thread_id`** 对应入站事件的 `root_id` / `parent_id` / `thread_id`；其中 `feishu_root_id` 与历史方案里口头说的「reply_root / feishu_reply_root_id」语境一致（话题根消息 id）。

详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

### 已知限制与风险

- **`receive_id_type`**：机器人主循环出站仍以 **`chat_id`** + 规范化后的 `oc_`/`ou_` 为主；**内置飞书工具**发 `create` 消息时可经 `MINIAGENT_FEISHU_RECEIVE_ID_TYPE`、工具参数或 `AgentConfig.feishu_im_receive_id_type` 使用 `open_id`/`union_id`，须与传入的 `receive_id` 类型一致。
- **卡片回调 `p2.card.action.trigger`**：依赖按钮 `action.value` 与回调 `context.open_chat_id` 等字段；无内置幂等键；生产使用需在开放平台完成订阅与卡片配置，必要时在业务 `value` 中自带去重键。

## API 调用

飞书消息发送通过飞书开放平台 API：

```python
# 发送思考过程
POST https://open.feishu.cn/open-apis/im/v1/messages
Content-Type: application/json
Authorization: Bearer <tenant_access_token>

{
    "receive_id": "oc_xxx",
    "msg_type": "interactive",
    "content": "{\"elements\":[{\"tag\":\"div\",\"text\":{\"content\":\"思考内容\"}}]}"
}
```

## 常见问题

### 飞书未启动

检查环境变量是否配置完整：
```bash
echo $FEISHU_APP_ID
```

### receive_id 无效（Error 230001）

确认传入的 `chat_id` 格式正确：
- 群聊：`oc_` 开头
- 用户：`ou_` 开头

### 思考过程未发送或更新失败

群聊且 `is_feishu=True` 时才会推送思考卡片。若内容始终为空则不会发。若 PATCH 失败（权限、频率等），日志中会有 `更新思考消息失败`；飞书对单条消息可 PATCH 次数有限，实现上已做时间与字数节流。

## 命令参考

| 命令 | 说明 |
|------|------|
| `.feishu start` | 启动飞书连接 |
| `.feishu stop` | 停止飞书连接 |
| `.feishu status` | 查看状态 |

## 架构说明

飞书运行时位于 `miniagent/engine/feishu_state.py`（`FeishuRuntime`）；`feishu_runtime.py` 仅为兼容重导出。历史上单文件 `unified.py` 已移除，入口请使用 `miniagent.compat.unified_entry` / `python -m miniagent`。

## 调研与路线图：交互卡片 JSON v2

### 现状

- 文本 Agent 回复与思考卡片经 [`miniagent/feishu/poll_server.py`](../miniagent/feishu/poll_server.py) 发送 `msg_type=interactive`，正文为旧式结构中的 `elements[].tag=div` + `text.tag=lark_md`。
- 宽 GFM 表在列数超阈值时降级为提示 + 代码块内等宽文本表（`MINIAGENT_FEISHU_TABLE_FALLBACK`），不依赖 v2。

### v2 能力（开放平台）

- 卡片 JSON 2.0 提供 `column_set`、`table` 等容器，适合结构化表格与分栏（需客户端版本支持，见开放平台「component JSON v2.0」文档）。
- **与当前路径的关系**：v2 与现有 `lark_md` 单 `div` 混排在同一条 `interactive` 消息内的兼容性需按官方 schema 实测；整体升级为 v2 卡片或「第二张卡片专发 table」会涉及较大改造与回退策略。

### 建议

- 短期：继续优化 `lark_md` + 文本表降级（已实现）。
- 中长期：若需「真表格」预览，单独立项：解析 GFM → 生成 v2 `table`/`column_set`，限定行列上限，失败时回退 `lark_md`。

## 相关文档

- [ENGINEERING.md](ENGINEERING.md)：可选安装 `pip install -e ".[dev,feishu]"`、CI 飞书 job 说明。
- [SECURITY.md](SECURITY.md)：飞书凭证与 `.env` 要求。
- [DEPLOYMENT.md](DEPLOYMENT.md)：部署与依赖。
