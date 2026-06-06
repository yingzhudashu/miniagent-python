# 通道绑定 (Channel Binding)

> Mini Agent Python | 版本: 2.0.3 | CLI 与飞书通道如何映射到同一会话

## 设计原理

- **CLI**：启动时由 `init_subsystems` 将 `__cli__` 绑定到默认会话，并 `set_primary`；**`/session switch` 成功后会同步** `__cli__` 与「自动跟随」的飞书私聊 sender 到同一目标会话。
- **飞书群聊**：始终使用独立会话 `feishu:<chat_id>`。CLI 可通过 **`/session switch oc_xxx`**（自动规范为 `feishu:oc_xxx`）聚焦到某群会话。
- **飞书私聊**：首条私聊到达时，若 `feishu_p2p:<sender_id>` 尚未绑定，则**自动绑定**到当前 `active_session_id`；sender 记入 `feishu_p2p_synced_senders`，之后随 `/session switch` 一起重绑。

不再提供 `/bind` / `/unbind` 用户命令；查看当前映射请用 **`/status`**。

`ChannelRouter` 实现跨通道共享：

- **记忆共享**：对话历史、事实提取、摘要互通
- **文件共享**：CLI 与飞书访问同一会话 `files/`
- **工具共享**：会话级工具对绑定通道可见

## 架构

```
┌─────────────────────────────────────────────────┐
│                ChannelRouter                     │
│  __cli__  ──bind──►  primary_session            │
│  feishu_p2p:ou_x ──bind──►  (同上，自动/随 switch) │
│  feishu:oc_xxx  ◄── 群聊独立会话（switch 可聚焦 CLI）│
└─────────────────────────────────────────────────┘
```

## 典型场景

### CLI 为主，飞书私聊辅助

1. 在 CLI 使用 `default` 或任意工作会话
2. 手机上向机器人发**首条私聊** → 自动绑定到当前 `active_session_id`
3. CLI 与私聊共享记忆；`/session switch` 后，已自动跟随的 sender 一并切换

### 飞书群为主，CLI 干预

```bash
> /session switch oc_xxxxxxxxxxxxx
```

CLI 聚焦该群会话（`feishu:oc_xxx`）：终端仅显示该群入站预览与思考镜像；其它群仍在后台处理。

## 命令与诊断

| 操作 | 命令 |
|------|------|
| 切换工作会话（含飞书群 `oc_xxx`） | `/session switch <编号/ID>` |
| 查看绑定与 CLI 聚焦模式 | `/status` |

**会话标识**：编号（`1`）、原始 ID（`default`）、飞书群（`oc_xxx` 或 `feishu:oc_xxx`）。

## CLI 显示策略（`cli_feishu_policy`）

| CLI 聚焦模式 | 判定条件 | 群聊 CLI 预览 | 私聊 CLI 预览 |
|--------------|----------|---------------|---------------|
| **一般模式** | CLI 绑定非 `feishu:oc_*` 会话 | 否（后台仍回复） | 是（私聊已绑定到与 CLI 同会话） |
| **飞书群聊聚焦** | CLI 绑定 `feishu:<chat_id>` | 是（仅当前群） | 否 |

`/status` 会显示通道绑定列表与聚焦模式说明（原 `/bind status` 信息）。

## 与 Session 系统的关系

```
用户输入 (CLI / 飞书)
    ↓
ChannelRouter.resolve() → session_key
    ↓
SessionManager.get_or_create(session_key)
    ↓
UnifiedEngine.run_agent_with_thinking(...)
```

**约束**：

1. 群聊路由键始终为 `feishu:<chat_id>`，不参与私聊自动绑定逻辑。
2. `/session switch` 到飞书群时，若磁盘尚无该会话，会创建占位会话再绑定 CLI。
3. 绑定状态持久化在 `{project_state}/channel-router.json`（含 `last_cli_session` 等）。
