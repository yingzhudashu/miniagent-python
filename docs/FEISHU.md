# 飞书集成文档

> Mini Agent Python | 版本: 2.1.0 | 飞书 WebSocket 长连接

## 快速开始

### 1. 配置凭据

在 **项目根** 创建 `config.user.json`（可从 `config.defaults.json` 复制），填写 `secrets`：

```json
{
  "secrets": {
    "feishu_app_id": "cli_xxx",
    "feishu_app_secret": "xxx",
    "feishu_verification_token": "xxx"
  }
}
```

`env_loader` 会将上述值桥接到 SDK 所需的 `FEISHU_*` 环境变量。

### 2. 启动

```bash
python -m miniagent --feishu
```

或在 CLI 中运行：`/feishu start`

**启动形态**：进程始终以 **CLI 主循环** 为主；上述两种方式均为 **CLI + 飞书**（同进程内附加飞书 WebSocket 长连接），不存在无 CLI 的独立飞书进程入口。

在全屏 prompt_toolkit CLI 下，飞书启动提示、以及**策略允许**的入站横幅与思考镜像，会写入上方 **transcript**（`RuntimeContext.cli_transcript_append`），而不再向裸 stdout `print`，避免与备用屏输入行互相覆盖。

**CLI 显示隔离**（详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md) §CLI 显示策略）：默认 CLI 在 `default` 等一般会话时，**群聊**消息仅在飞书侧处理与回复，**不会**刷屏到 CLI；仅与 CLI 同会话的**私聊**会显示预览。使用 **`/session switch oc_xxx`**（或 `feishu:oc_xxx`）进入群聊聚焦后，CLI 只显示该群内容，私聊不再接入或显示。

`get_logger()` 的诊断输出写入 **stderr**（不再写 stdout）；飞书 WebSocket 客户端 SDK 日志级别为 **ERROR**，避免与全屏 UI 争用终端。

全屏 CLI 运行时会暂时把 ``get_logger`` 控制台输出提高到 **WARNING**（集成终端里 stderr 仍会打乱备用屏）。调试若需要 INFO/DEBUG，可设置 **`features.tui_verbose_log=true`**。

在飞书里发送以 ``/`` 开头的命令时，默认 `/session switch` / `create` / `rename` 以及 `/schedule` 的 `add`/`update`/`remove`/`enable`/`disable` **不会**修改与本地 CLI 共享的 ``active_session_id`` 或 ``tasks.json``，仅返回提示；``/stop`` 亦默认拒绝（避免远程结束进程）。请在本地 MiniAgent 终端执行，或设置 **`feishu.dot_commands_full=true`** 放开全部点命令（启动时会打 WARNING；群聊误触风险需自行管控）。启用 FULL 后飞书侧 `/stop` 成功即进程退出，通常**不会**再收到第二条飞书确认消息。调试 HTTP 栈时请勿开启 ``HTTPX_LOG_LEVEL=debug`` 等会把第三方日志打到终端的配置，以免干扰全屏 UI。

Agent 在飞书会话中若通过内置工具 **`run_dot_command`** 调点命令，上述限制与直接发点命令一致（默认 `cli_dispatch_allow_mutations=False`；`feishu.dot_commands_full=true` 时为 True）。不需要该能力时可将 **`cli.dot_tools_enabled=false`**，启动时不再注册该工具（见 `config.defaults.json`）。

## 运维速查（WebSocket）

连接由 `poll_server` + `ws_health` 监督；默认关闭 SDK 内建 `auto_reconnect`，断线后由 `FeishuRuntime` 外层退避重建。Windows 上 `WinError 121`、收包循环退出等日志后若出现「约 Xs 后重连」，属**预期自愈**。

| JSON 路径 | 默认 | 说明 |
|-----------|------|------|
| `feishu.websocket.auto_reconnect` | `false` | 启用 SDK 内建重连（不推荐） |
| `feishu.websocket.watchdog_interval` | `30` | 看门狗轮询（秒）；**非**多实例注册心跳 |
| `feishu.websocket.dead_conn_grace` | `90` | 连接为空超过该秒数则重建 |
| `feishu.websocket.reconnect_grace` | `300` | 仅 `auto_reconnect=true` 时生效 |
| `feishu.websocket.refresh_interval` | `0` | 定期主动刷新；不稳定网络可设 `3600` |
| `feishu.websocket.idle_refresh` | `0` | 无入站超过 N 秒刷新（默认关） |

私聊绑定与排障见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)；Windows 专项见下文「Windows / 长连接」。

## 架构

```
飞书开放平台
    │ WebSocket 长连接
    ▼
miniagent/feishu/poll_server.py
    │
    ▼
miniagent/engine/feishu_handler.create_feishu_handler() → (text_handler, media_handler)
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
    └── 飞书（群聊与私聊）: 每轮 LLM 思考 **一条交互卡片**（流式 PATCH 节流；`finalize` 时若超长则 **首张 PATCH + 后续多张「思考中 (k/n)」续页**）；同轮工具意图默认 **追加到该卡片**（Internal 常量 `EXECUTION_THINKING_MERGE_TOOLS`）。最终回复按 `feishu.card.body_max_chars`（默认约 48k 字符）**分片多张卡片**；任一分片发送失败则 **中止后续分片**，已发部分不再用整条 `text` 重复回退；仅当交互消息 **一条都未成功** 时才按同上限 **分条 text** 回退全文（由 ``feishu_handler`` 委托 ``poll_server._send_reply``）。**Phase 3 反思评估**（`features.reflection` 默认开启）完成后，评估结果以 **尾部文本** 并入最终回复卡片/正文，**不再**单独发送质量评估卡片。
```

**多群并行**（默认开启）：不同飞书群映射到独立 `session_key`（`feishu:<chat_id>`），在 `agent.parallel_sessions=true` 时可**同时**运行 Agent（进程内默认最多 4 路，见 `agent.max_parallel_sessions`）。同一群内消息仍按 `chat_id` 队列串行。设 `agent.parallel_sessions: false` 可回退为全局串行。

### 配置项（`config.defaults.json` → `feishu` 节）

| JSON 路径 | 含义 |
|-----------|------|
| `feishu.card.thinking_max_chars` / `feishu.card.body_max_chars` | 单张交互卡片正文上限（Advanced）；完整文本仍在 **history.json** |
| `memory.thinking_for_llm_mode` | `thinking` 历史回灌给 LLM 的模式：`off` / `compact` / `full`；默认 `compact` |
| `memory.thinking_for_llm_compact_max_chars` | `compact` 模式下 thinking 摘要最大字符数，默认 1200 |
| `memory.thinking_for_llm_max_chars` | 仅 `full` 模式使用，控制完整 thinking 正文回灌上限 |
| `feishu.markdown_commands` | `true` 时飞书侧部分命令使用 Markdown 表格（默认 `false`）；或环境变量 **`MINIAGENT_FEISHU_MARKDOWN_COMMANDS=1`** |
| `feishu.dot_commands_full` | `true` 时飞书点命令与 CLI 同等（含 `/stop`；默认 `false`） |
| `cli.dot_tools_enabled` | `false` 时不注册 `run_dot_command` |
| `feishu.reply_plain` | `true` 时弱化最终回复 Markdown |
| `feishu.reply_target` | 默认 **`reply`**；`create` 为会话内新建消息 |
| `feishu.reply_in_thread` | 与 `reply` 联用；未设置且入站 `thread_id` 非空时默认话题内回复 |
| `feishu.card_action_router` | 默认 **开**；处理卡片按钮回调 |
| `feishu.tools_explicit` / `feishu.tools_auto` | 内置飞书工具注册策略（见 `config.defaults.json`） |
| `feishu.doc.docx_url_prefix` | 创建云文档成功时附带可分享链接 |
| `feishu.receive_id_type` | IM `create` 的 `receive_id_type` |
| `feishu.doc.folder_token` | 云盘默认父文件夹 token |
| `feishu.doc.folder_fallback_root_meta` | 无 token 时尝试根目录元数据 API |
| `feishu.card_extract_inbound` | 入站 `interactive` 抽取可读文本 |
| `secrets.feishu_user_access_token` | 用户 OAuth token；`feishu_doc` + `action=search` 必填 |

工具意图合并、卡片 v2 等细节为 Internal 常量，见 `miniagent/core/constants.py`。

### 出站能力矩阵（摘要）

| 能力 | 实现要点 |
|------|----------|
| 会话内新消息 / 回复某条消息 | `create` 与 `im/v1/messages`；`reply` 与 `im/v1/messages/:id/reply`（见上表环境变量） |
| 上传并发 file/image 消息 | `miniagent/feishu/upload_io.py` + 工具 `feishu_send_workspace_file`（需 `feishu.tools_explicit=true` 或 `feishu.tools_auto` 与 `secrets.feishu_app_id`/`secrets.feishu_app_secret`） |
| 云文档（聚合工具） | **`feishu_doc`**：见下表；实现 [`miniagent/feishu/docx/`](../miniagent/feishu/docx/)、权限/搜索 [`drive_extra.py`](../miniagent/feishu/drive_extra.py) |
| 多维表格 | **`feishu_bitable`**：`get_meta`/`list_fields`/`list_records`/`get_record`/`create_record`/`update_record`/`delete_record`/`upload_attachment`；[`bitable/`](../miniagent/feishu/bitable/) |
| 互动卡片 | **`feishu_send_interactive_card`**、**`feishu_update_message_card`**；构建/入站抽取 [`cards/`](../miniagent/feishu/cards/) |
| 云盘列举 | `feishu_list_drive_files` + [`drive_client`](../miniagent/feishu/drive_client.py) |
| 撤回机器人消息 | `feishu_recall_message` |

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
2. **工具开关**：`feishu.tools_explicit=true`，或 **未设置** `feishu.tools_explicit` 且默认已开的 `feishu.tools_auto`（需 `secrets.feishu_*`）。显式 `feishu.tools_explicit=false` 或 `feishu.tools_auto=false` 可关闭。凭证齐全但未注册扩展工具时，进程会打 **INFO** 自检日志指向本节。
3. **凭证**：`FEISHU_APP_ID`、`FEISHU_APP_SECRET` 已配置（与 WebSocket 长连接使用同一应用）。
4. **权限**：应用已开通上表 IM / docx / drive 等能力，机器人已进群（群聊），租户未禁用量级调用。
5. **会话 ID**：默认 `receive_id_type=chat_id`，工具使用当前回合的 **群/会话 `chat_id`**。若设置 `feishu.receive_id_type=open_id`（或 `union_id`），须与 **`receive_id` 同类型**；此时默认 `receive_id` 为入站注入的 **发送者 `sender_id`（通常为 open_id）**，缺省则须在工具参数中显式传入 `receive_id`。
6. **云文档目录**：`feishu_doc` + `action=create` 与 `feishu_list_drive_files` 需要云盘**父文件夹** `folder_token`；可直接传 token，或传飞书云盘**文件夹分享链接**（工具会解析路径中的 `folder/<token>`）；也可配置 `feishu.doc.folder_token`。若仍无 token，默认会尝试根目录元数据 API（`FEISHU_DOC_FOLDER_FALLBACK_ROOT_META`，默认开；`0` 关闭）。仍失败时按工具返回的「已尝试」项排查。
7. **搜索**：`feishu_doc` + `action=search` 需配置 `secrets.feishu_user_access_token`；无 token 时工具返回明确错误（含 `requires_user_token` 提示）。
8. **权威说明**：[飞书权限列表](https://open.feishu.cn/document/server-docs/docs/scope) 以开放平台当前文档为准。

### 集成验证建议（Bot vs User Token）

下列能力在 CI 中仅 **mock 单测**；上线前请在真实租户用测试应用验证一次：

| 能力 | Token | 注意 |
|------|-------|------|
| `feishu_doc` 表格 `create_table` / `write_table_cells` | 租户 Bot | `batch_update` 的 cell payload 须与当前开放平台文档一致；失败时查看工具返回中的 `code`/`msg` |
| `feishu_bitable` `upload_attachment` | 租户 Bot | 附件字段须先 `list_fields` 确认为附件类型；`parent_type=bitable_file` 若无效可对照文档改为 `bitable_image` 等 |
| `feishu_doc` `search` | **用户** `secrets.feishu_user_access_token` | Bot token 不可用；无 token 时返回 JSON `requires_user_token: true` |
| `list_permissions` / `add_permission` / `remove_permission` | 通常 Bot（视租户策略） | 部分租户仅所有者可改权限 |

### `feishu_doc` action 一览

| 类别 | action |
|------|--------|
| 基础 | `create`, `get`, `read`, `write`（`mode=replace` 整篇替换）, `append`, `delete` |
| 块 | `list_blocks`, `get_block`, `update_block`, `delete_block`, `batch_update` |
| 工作区 I/O | `export_raw`, `import_raw` |
| 表格 | `create_table`, `write_table_cells`, `create_table_with_values` |
| 媒体 | `upload_image`, `upload_file`, `download_media`, `upload_image_from_message` |
| 云盘 | `copy`, `move` |
| 协作 | `list_permissions`, `add_permission`, `remove_permission` |
| 发现 | `search`（需 User Token） |

### Markdown 写入与渲染说明

飞书云文档使用 **Block-based 结构**，不是直接写入 Markdown 文本。MiniAgent 提供两种渲染模式：

#### 1. 富文本渲染模式 (`render_mode="rich"`)

**默认模式**，将 Markdown 自动转换为飞书文档块结构，保留格式：

| Markdown 元素 | 飞书 Block 类型 |
|---------------|-----------------|
| `# 标题` | HEADING1-6 |
| `**粗体**` | TextRun + bold 样式 |
| `*斜体*` | TextRun + italic 样式 |
| `[链接](url)` | TextRun + link 样式 |
| `` `代码` `` | TextRun + inline_code 样式 |
| ``` 代码块 ``` | CODE 块 + language 属性 |
| `- 列表项` | BULLET 块 |
| `1. 列表项` | ORDERED 块 |
| `> 引用` | QUOTE 块 |
| `| 表格 |` | TABLE 块 |

**使用示例**：

```json
{
  "action": "write",
  "doc_token": "doc_xxx",
  "content": "# 报告标题\n\n**要点：**\n- 第一项\n- 第二项\n\n```python\nprint('示例代码')\n```",
  "render_mode": "rich"
}
```

#### 2. 纯文本模式 (`render_mode="plain"`)

向后兼容模式，剥离 Markdown 标记，仅保留纯文本内容。

#### 3. 导入 Markdown 文件 (`import_raw`)

从工作区 Markdown 文件导入到云文档，默认使用富文本渲染：

```json
{
  "action": "import_raw",
  "doc_token": "doc_xxx",
  "relative_path": "files/report.md",
  "render_mode": "rich"
}
```

**最佳实践**：
- 写入 Markdown 内容时使用 `render_mode="rich"`（默认）
- 导入 Markdown 文件时使用 `import_raw` + `render_mode="rich"`
- 需要纯文本时可设置 `render_mode="plain"`（向后兼容）

#### Docx validation fallback

`code=1770001 msg=invalid param` and `code=99992402 msg=field validation failed`
mean Feishu rejected a rich Docx block payload. MiniAgent treats both as rich
render validation failures, records a warning with the code/msg/log_id when
available, and retries the same Markdown as readable plain text if no rich
block has been written yet.

If an earlier rich block batch already succeeded, MiniAgent stops the remaining
rich writes and returns the partial success count plus warnings. It does not
write the full plain-text document again, because that would duplicate content.
The `feishu_doc` result includes `meta.render_stats.written_blocks`,
`meta.render_stats.fallback_count`, and `meta.warnings` for diagnostics.

### 互动卡片按钮 `action.value` 示例

```json
{
  "miniagent_text": "确认执行",
  "chat_id": "oc_xxx",
  "action_id": "confirm_run",
  "chat_type": "group",
  "dedupe_key": "run-2026-05-21-1"
}
```

无 `miniagent_text` 时，若提供 `action_id`（及可选 `form`/`form_value`），仍会合成 `[卡片操作] action_id=…` 文本并调度 Agent（需 `feishu.card_action_router`）。

### 飞书与会话工作区文件（发附件）

内置工具 `feishu_send_workspace_file` 的 `relative_path` 必须是**相对当前会话工作区根**的路径（与 `SessionManager` 为该会话分配的 `files_path` 一致，通常为 `…/sessions/<safe_id>/files/`）。飞书用户发到机器人的 file/image 经入站 `media_handler` 保存到 **`files/feishu_incoming/`** 下，发送时应使用该相对路径（例如 `files/feishu_incoming/报告_msgid.pdf`）。

**不是**用户操作系统上的任意绝对路径。若需发送尚未在工作区内的内容，应先用会话内的文件读写工具写入 `files/` 后再调用发送工具。

## chat_type 与入站结构体

生产路径中文本入站使用单参 **`FeishuInboundText`**（定义见 [`miniagent/feishu/types.py`](../miniagent/feishu/types.py)），其中字段 **`chat_type`** 区分群聊与私聊；不再向 handler 单独传入 `chat_type` 位置参数。

| chat_type | 行为 | session_key |
|-----------|------|------------|
| `group` | 独立会话，始终创建/使用 `feishu:<chat_id>` | `feishu:<chat_id>` |
| `p2p` | 若尚未绑定，则自动 `bind(feishu_p2p:<sender>, active_session_id)`；已绑定则使用目标 session_key | 通常为当前 CLI 活跃会话 |

**飞书入站独占**：同一 `paths.state_dir` 下通过 `workspaces/feishu_inbound_owner.json`（或 `paths.state_dir` 根目录下的 `feishu_inbound_owner.json`）保证**仅一个存活进程**可成功执行 `/feishu start` 并持有常驻重连任务。避免多开实例重复收消息。

**常驻与锁**：`FeishuRuntime` 在后台任务中循环调用 `start_feishu_poll_server`；单次 WebSocket 断线或启动失败会**指数退避后自动重连**，此期间**不释放入站锁**（其它进程仍无法抢占）。仅在执行 `/feishu stop`、任务被取消或进程退出路径上释放锁。

**WebSocket 会话监督**（`miniagent/feishu/ws_health.py`）：连接成功后由看门狗监督收包任务与连接状态；收包循环退出、连接长时间为空、或达到定期刷新间隔时，会结束当前会话并由 `FeishuRuntime` 外层退避重建。默认**关闭** SDK 内建 `auto_reconnect`，避免与外层重连脱节导致「进程显示运行中但收不到消息」。`/feishu status` 可查看上次会话结束原因与最后入站时间。

### Windows / 长连接

Windows 上可能出现 `OSError: [WinError 121]` 或日志 `receive message loop exit` / `no close frame received or sent`（网络休眠、VPN、NAT、网卡节能等）。增强后这些日志后通常会紧跟 `飞书 WS 会话监督结束` 与 CLI `约 Xs 后重连`，属**预期自愈**，不等于进程崩溃。WebSocket 环境变量表见上文 [运维速查（WebSocket）](#运维速查websocket)。

运维建议：电源计划中避免网卡「允许计算机关闭此设备以节约电源」；不稳定网络可设 `feishu.websocket.refresh_interval=3600`。

**私聊绑定**：首条私聊消息**自动绑定**到当前 `active_session_id`；已跟随的 sender 会随 **`/session switch`** 与 CLI 一起重绑。查看映射与聚焦模式请用 **`/status`**（见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)）。

## 消息处理流程

### 文本（`message_type == text`）

1. **接收消息** — WebSocket 长连接接收事件
2. **提取内容** — 解析 `chat_id`、`sender_id`、`chat_type`、消息文本，以及开放平台事件中的 **`message_id`**、**`root_id`**、**`parent_id`**、**`thread_id`**（后三者可能为空；用于话题上下文与 `feishu.reply_target=reply` 时的默认话题内回复策略）
3. **命令拦截** — 以 `/` 开头的消息路由到 `dispatch_command()`（默认拒绝会话/定时任务变异与 `/stop`；``feishu.dot_commands_full=true`` 时与 CLI 同等）。例如 `/help` 与可选 ``feishu.markdown_commands=true`` 下的 `/session list` 等返回 Markdown **表格**，依赖客户端对 GFM / `lark_md` 子集的支持；若表格显示异常可改用本地 CLI 或关闭该变量
4. **解析 session_key** — 通过 `ChannelRouter.resolve_feishu_message()`
5. **运行 Agent** — `run_agent_with_thinking(session_key, ...)`
6. **发送思考** — 与 CLI 一致由 `ThinkingDisplay` 驱动：`push_feishu_thinking_stream()` 对同一逻辑段 PATCH 节流更新；规划阶段为单 header ``[评估与计划]`` 的流式卡；执行阶段为 ``[执行]`` 或分步时的 ``[步骤 i/n] …``；同段内工具结果走 `append_feishu_thinking_same_card()`；阶段切换时仅 `finalize_feishu_thinking_stream()` 收尾当前卡（不另发空思考卡）；**finalize 与 PATCH 共用 `_prepare_thinking_body_for_card`**（折叠空行与 `lark_md` 规范化一致，正文顶格无额外段首/列表缩进）。非流式结论块仍为 `finalize` + `_send_thinking()`。详见 `miniagent/feishu/poll_server.py` 顶部常量（PATCH 频率与单条可 PATCH 次数上限）
7. **发送回复** — `_send_reply()` 使用与思考相同的交互卡片构建（`_feishu_interactive_card_dict` + `_prepare_card_markdown`）；入站 `chat_id` 会先 `_normalize_im_receive_chat_id`，仅当规范化后以 `oc_`（群）或 `ou_`（用户）开头时才发送。

若 `message_handler` 抛错或 `_send_reply` 失败，**不会**把该 `message_id` 写入磁盘去重，便于同一事件在可恢复场景下再次处理。

### 模型输出与 lark_md

飞书交互卡片正文为 **`lark_md` 子集**，并非完整 GFM。发送前会经 `_normalize_lark_md()` 做保守处理（零宽字符、`<br>`、过长围栏、**所有** GFM 表格等）。为获得最佳展示，建议模型：

- 优先使用**短列表**。GFM 管道符表格在飞书 `lark_md` 中不受支持，**所有表格**自动转为 **bullet-point list** 格式（窄表：`- 值1 | 值2 | 值3`；宽表：`- **列名** → 值1, 列名2=值2, ...`），所有数据保留。
- ATX 标题（`#`, `##`, `###` 等）自动转为**粗体**（`**标题**`），因为 `lark_md` 不支持标题语法。
- 代码块使用标准 **三个反引号** 围栏。
- 单独成行的 `---` / `***` / `___`（GFM 分隔线）会替换为横线字符，便于在不支持 ``hr`` 的客户端内阅读。
- 若交互卡片发送失败而回退为 **`msg_type=text`**，飞书客户端**不会**把正文当 Markdown 渲染（纯文本展示），属平台能力限制而非模型未使用 Markdown。日志中会 **WARNING** `飞书发送 msg_type=text 回退（无 lark_md 渲染）: reason=…` 便于区分「渲染降级」与「API 失败」。

### 文件与图片（`file` / `image`）

- 经同一 `chat_id` 消息队列串行调用 `media_handler`：按与文本相同的规则解析 `session_key`、私聊自动绑定，调用开放平台 **获取消息中的资源文件** 接口下载后，写入该会话工作区下的 **`feishu_incoming/`** 目录（与 `SessionManager` 中会话的 `files_path` 一致，即 `workspaces/sessions/<safe_id>/files/feishu_incoming/`）。
- 下载或处理失败（`media_handler` 返回以「⚠️」开头的提示）时，**不写入磁盘去重**，避免永久跳过。
- **`feishu.media.run_agent`**：设为 `1` / `true` / `yes` / `on` 时，在成功落盘后追加一条合成用户消息并调用 `run_agent_with_thinking`。
- **`feishu.media.vision_desc`**：默认开启（`1`/`true`）。收到图片消息时，先调用视觉模型（多模态 LLM）生成图片的文字描述，再将描述注入上下文交给 Agent 处理。设为 `0` 可关闭，此时仅保存图片文件而不生成描述。
- **`feishu.media.silent_reply`**：同上真值时，落盘成功仍**不向飞书发送** `_send_reply`（CLI 镜像日志不受影响）。
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
- **私聊**（`p2p`）：未绑定时可自动绑到 CLI 活跃会话，与 CLI **共享**同一 `session_key`；已手动绑定或 `/session switch` 后行为见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

### chat_type 支持（私聊 vs 群聊）

系统通过 **`FeishuInboundText.chat_type`**（或与媒体路径等价的元数据）区分飞书消息类型：

| chat_type | 会话策略 | session_key 格式 | 参与绑定 |
|-----------|----------|------------------|----------|
| `group` | 始终独立会话 | `feishu:<chat_id>` | 否 |
| `p2p` | 检查绑定映射 | `feishu_p2p:<sender_id>` | 是 |

- **群聊**：每个群自动创建独立会话，多群完全隔离
- **私聊**：首条消息自动绑定到当前 `active_session_id`；`/session switch` 后已跟随 sender 同步重绑
- **绑定效果**：私聊消息使用绑定会话的上下文；CLI 终端实时打印预览（诊断见 `/status`）

**Agent 配置字段**：`AgentConfig` 中的 **`feishu_root_id`** / **`feishu_parent_id`** / **`feishu_thread_id`** 对应入站事件的 `root_id` / `parent_id` / `thread_id`；其中 `feishu_root_id` 与历史方案里口头说的「reply_root / feishu_reply_root_id」语境一致（话题根消息 id）。

详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

### 已知限制与风险

- **`receive_id_type`**：机器人主循环出站仍以 **`chat_id`** + 规范化后的 `oc_`/`ou_` 为主；**内置飞书工具**发 `create` 消息时可经 `feishu.receive_id_type`、工具参数或 `AgentConfig.feishu_im_receive_id_type` 使用 `open_id`/`union_id`，须与传入的 `receive_id` 类型一致。
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
| `/feishu start` | 启动飞书连接 |
| `/feishu stop` | 停止飞书连接 |
| `/feishu status` | 查看状态 |

## 架构说明

飞书运行时位于 `miniagent/engine/feishu_state.py`（`FeishuRuntime`）；`poll_server.py` 负责 WebSocket 长轮询事件分发。入口请使用 `python -m miniagent`。

## 互动卡片（`cards/`）

- 出站/思考：v1 `lark_md`（[`cards/builder.py`](../miniagent/feishu/cards/builder.py)）。
- 入站抽取与按钮路由：[`cards/extract.py`](../miniagent/feishu/cards/extract.py)、[`cards/action_router.py`](../miniagent/feishu/cards/action_router.py)。
- GFM 表格：**所有表格**转为 **bullet-point list**（[`cards/gfm_table.py`](../miniagent/feishu/cards/gfm_table.py) 中的 `gfm_table_block_to_bullet_list`）；窄表用 `|` 分隔，宽表用 key-value 格式。不再使用警告提示或代码块包裹。

## 相关文档

- [ENGINEERING.md](ENGINEERING.md)：可选安装 `pip install -e ".[dev,feishu]"`、CI 飞书 job 说明。
- [SECURITY.md](SECURITY.md)：飞书凭证与配置要求。
- [DEPLOYMENT.md](DEPLOYMENT.md)：部署与依赖。

---

## 工具 API 参考

### feishu_doc（飞书云文档聚合工具）

单一工具，通过 `action` 参数执行 26 种操作。

**基础操作**

| Action | 参数 | 权限 |
|--------|------|------|
| `create` | `title`, `folder_token`(可选), `folder_share_url`(可选) | 需创建权限 |
| `get` | `document_id` | 需访问权限 |
| `read` | `document_id` | 需读取权限 |
| `write` | `document_id`, `text`, `mode`(append/replace), `render_mode`(rich/plain) | 需编辑权限 |
| `append` | `document_id`, `text`, `render_mode`(rich/plain) | 需编辑权限 |
| `delete` | `document_id` | 需管理权限 |

**渲染模式说明（render_mode）**

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `rich`（默认） | Markdown 富文本渲染：标题、粗体、列表、代码块、表格等 | 大多数场景，需要格式化内容 |
| `plain` | 纯文本模式：仅移除 `#`、`>` 等标记 | 向后兼容、简单文本追加 |

支持的 Markdown 元素（`render_mode=rich`）：
- 块级：标题（#~######）、段落、列表（有序/无序）、代码块（带语言标记）、引用、表格、分隔线
- 内联：粗体（`**text**`）、斜体（`*text*`）、删除线（`~~text~~`）、链接（`[text](url)`）、内联代码（`` `code` ``）

**Block 操作**

| Action | 参数 | 权限 |
|--------|------|------|
| `list_blocks` | `document_id` | 需读取权限 |
| `get_block` | `document_id`, `block_id` | 需读取权限 |
| `update_block` | `document_id`, `block_id`, `text` | 需编辑权限 |
| `delete_block` | `document_id`, `block_id` | 需编辑权限 |
| `batch_update` | `document_id`, `operations`(JSON 数组) | 需编辑权限 |

**导入/导出**

| Action | 参数 | 权限 |
|--------|------|------|
| `export_raw` | `document_id` | 需导出权限 |
| `import_raw` | `document_id`, `raw_content` | 需编辑权限 |

**表格操作**

| Action | 参数 | 权限 |
|--------|------|------|
| `create_table` | `document_id`, `rows`, `cols` | 需编辑权限 |
| `write_table_cells` | `document_id`, `table_block_id`, `cells` | 需编辑权限 |
| `create_table_with_values` | `document_id`, `headers`, `rows` | 需编辑权限 |

**媒体操作**

| Action | 参数 | 权限 |
|--------|------|------|
| `upload_image` | `document_id`, `image_path` | 需编辑权限 |
| `upload_file` | `document_id`, `file_path` | 需编辑权限 |
| `download_media` | `document_id`, `media_token` | 需读取权限 |
| `upload_image_from_message` | `document_id` | 需编辑权限 |

**云盘操作**

| Action | 参数 | 权限 |
|--------|------|------|
| `copy` | `document_id`, `target_folder_token` | 需源读取+目标编辑权限 |
| `move` | `document_id`, `target_folder_token` | 需源管理+目标编辑权限 |

**协作者管理**

| Action | 参数 | 权限 |
|--------|------|------|
| `list_permissions` | `document_id` | 需管理权限 |
| `add_permission` | `document_id`, `member_type`, `member_id`, `permission` | 需管理权限 |
| `remove_permission` | `document_id`, `member_type`, `member_id` | 需管理权限 |

**搜索**

| Action | 参数 | 权限 |
|--------|------|------|
| `search` | `query` | 需 `secrets.feishu_user_access_token` |

### feishu_bitable（飞书多维表格聚合工具）

单一工具，通过 `action` 参数执行 8 种操作。

| Action | 参数 | 权限 |
|--------|------|------|
| `get_meta` | `app_token` | 需访问权限 |
| `list_fields` | `app_token`, `table_id` | 需读取权限 |
| `list_records` | `app_token`, `table_id`, `page_token`(可选), `field_names`(可选) | 需读取权限 |
| `get_record` | `app_token`, `table_id`, `record_id` | 需读取权限 |
| `create_record` | `app_token`, `table_id`, `fields` | 需编辑权限 |
| `update_record` | `app_token`, `table_id`, `record_id`, `fields` | 需编辑权限 |
| `delete_record` | `app_token`, `table_id`, `record_id` | 需编辑权限 |
| `upload_attachment` | `app_token`, `table_id`, `record_id`, `field_name`, `file_path` | 需编辑权限 |

### 独立工具

**feishu_send_interactive_card**

| 参数 | 必填 | 说明 |
|------|------|------|
| `receive_id` | 是 | 接收方 ID |
| `template` | 是 | 卡片模板名称 |
| `data` | 否 | 卡片模板变量 |
| `receive_id_type` | 否 | ID 类型，默认 `chat_id` |

**feishu_list_drive_files** — 列出云盘文件/目录（可选 `folder_token` 或 `folder_share_url`）

**feishu_recall_message** — 撤回消息（参数 `message_id`）

**feishu_send_workspace_file** — 发送工作区内文件到会话（参数 `file_path`）
