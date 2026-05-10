# 飞书集成文档

> Mini Agent Python — 飞书 WebSocket 长轮询

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

**启动形态**：进程始终以 **CLI 主循环** 为主；上述两种方式均为 **CLI + 飞书**（同进程内附加飞书长轮询），不存在无 CLI 的独立飞书进程入口。

在全屏 prompt_toolkit CLI 下，飞书启动提示、入站横幅、以及与 CLI 绑定同一会话时的「思考」镜像，都会写入上方 **transcript**（`RuntimeContext.cli_transcript_append`），而不再向裸 stdout `print`，避免与备用屏输入行互相覆盖。

`get_logger()` 的诊断输出写入 **stderr**（不再写 stdout）；飞书 WebSocket 客户端 SDK 日志级别为 **ERROR**，避免与全屏 UI 争用终端。

全屏 CLI 运行时会暂时把 ``get_logger`` 控制台输出提高到 **WARNING**（集成终端里 stderr 仍会打乱备用屏）。调试若需要 INFO/DEBUG，可设置环境变量 **`MINI_AGENT_TUI_VERBOSE_LOG=1`**。

在飞书里发送以 ``.`` 开头的命令时，``.session switch`` / ``create`` / ``rename`` 等**不会**修改与本地 CLI 共享的 ``active_session_id`` 或会话存储，仅返回提示；请在本地 MiniAgent 终端执行这些子命令。调试 HTTP 栈时请勿开启 ``HTTPX_LOG_LEVEL=debug`` 等会把第三方日志打到终端的配置，以免干扰全屏 UI。

## 架构

```
飞书开放平台
    │ WebSocket 长轮询
    ▼
miniagent/feishu/poll_server.py
    │
    ▼
create_feishu_handler() → handler(content, chat_id, sender_id, chat_type)
    │
    ▼
ChannelRouter.resolve_feishu_message(chat_id, sender_id, chat_type)
    │
    ├── 群聊: 返回 "feishu:<chat_id>" → 独立会话
    └── 私聊: 返回 "feishu_p2p:<sender_id>" → 检查是否绑定
    │
    ▼
UnifiedEngine.run_agent_with_thinking()
    │
    ├── CLI: 终端打印思考过程
    └── 飞书: 缓冲思考 → 完成后整批发送
```

## chat_type 支持

handler 函数支持 `chat_type` 参数，用于区分群聊和私聊：

| chat_type | 行为 | session_key |
|-----------|------|------------|
| `group` | 独立会话，始终创建/使用 `feishu:<chat_id>` | `feishu:<chat_id>` |
| `p2p` | 检查通道绑定，已绑定则使用绑定的 session_key | `feishu_p2p:<sender_id>` 或绑定的目标 |

**私聊绑定**：当 `feishu_p2p:<sender_id>` 已绑定到某会话时，
该用户的私聊消息将使用绑定的 session_key，实现与 CLI 或其他飞书用户的上下文共享。

## 消息处理流程

1. **接收消息** — WebSocket 长轮询接收事件
2. **提取内容** — 解析 `chat_id`、`sender_id`、`chat_type`、消息文本
3. **命令拦截** — 以 `.` 开头的消息路由到 `dispatch_command()`
4. **解析 session_key** — 通过 `ChannelRouter.resolve_feishu_message()`
5. **运行 Agent** — `run_agent_with_thinking(session_key, ...)`
6. **发送思考** — 缓冲模式：完成后通过 `_send_thinking()` 发送
7. **发送回复** — 通过 `_send_reply()` 发送最终回复

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

处理飞书侧消息时，每个 `chat_id` 自动创建独立会话，互不干扰。

### chat_type 支持（私聊 vs 群聊）

系统通过 `chat_type` 参数区分飞书消息类型：

| chat_type | 会话策略 | session_key 格式 | 参与绑定 |
|-----------|----------|------------------|----------|
| `group` | 始终独立会话 | `feishu:<chat_id>` | 否 |
| `p2p` | 检查绑定映射 | `feishu_p2p:<sender_id>` | 是 |

- **群聊**：每个群自动创建独立会话，多群完全隔离
- **私聊**：默认独立，可通过 `.bind feishu <sender_id> <会话>` 绑定到其他会话
- **绑定效果**：私聊消息使用绑定会话的上下文；CLI 终端实时打印预览

详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

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

### 思考过程未发送

当消息来自飞书会话时，思考过程会缓冲，完成后整批发送。如果内容为空，不会发送。

## 命令参考

| 命令 | 说明 |
|------|------|
| `.feishu start` | 启动飞书连接 |
| `.feishu stop` | 停止飞书连接 |
| `.feishu status` | 查看状态 |

## 架构说明

飞书运行时位于 `miniagent/engine/feishu_state.py`（`FeishuRuntime`）；`feishu_runtime.py` 仅为兼容重导出。历史上单文件 `unified.py` 已移除，入口请使用 `miniagent.compat.unified_entry` / `python -m miniagent`。
