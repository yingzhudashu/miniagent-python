# 通道绑定 (Channel Binding)

> Mini Agent Python — 将 CLI 与飞书通道绑定到同一会话，实现跨平台上下文共享

## 设计原理

默认情况下，每个输入通道拥有**独立会话**：
- CLI 输入 → `__cli__` 会话
- 飞书群聊 `oc_xxx` → `feishu:oc_xxx` 会话
- 飞书私聊 `ou_xxx` → `feishu_p2p:ou_xxx` 会话

通道绑定通过 `ChannelRouter` 将多个通道映射到**同一个主会话**，实现：
- **记忆共享**：跨通道的对话历史、事实提取、摘要全部互通
- **文件共享**：CLI 创建的文件，飞书会话可以直接访问
- **工具共享**：任一通道注册的会话级工具，另一通道立即可见

## 架构

```
┌─────────────────────────────────────────────────┐
│                ChannelRouter                     │
│                                                  │
│  ┌──────────┐   bind    ┌────────────────────┐  │
│  │ CLI      │ ─────────→│                    │  │
│  │ __cli__  │           │  primary_session   │  │
│  └──────────┘           │  (主会话 ID)       │  │
│                         │                    │  │
│  ┌──────────┐   bind    │  reverse index:    │  │
│  │ Feishu   │ ─────────→│  [CLI, feishu_p2p] │  │
│  │ p2p:ou_x │           └────────────────────┘  │
│  └──────────┘                                    │
│                                                  │
│  ┌──────────┐   不绑定   ┌────────────────────┐  │
│  │ Feishu   │ ──────────→│ feishu:oc_xxx      │  │
│  │ group    │  (独立会话) │ (独立会话，始终)    │  │
│  └──────────┘            └────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 使用场景

### 场景 1：CLI 为主，飞书辅助

你在 CLI 终端工作，但希望手机上通过飞书私聊也能看到同一上下文。

```bash
# 1. CLI 中绑定飞书私聊到你的主会话
> .bind feishu ou_xxxxxxxxxxxxx default

✅ 飞书私聊 (ou_xxxxxx...) 已绑定到: default

# 2. 现在你在手机上发飞书私聊，CLI 会打印预览
#    CLI 看到的对话和手机飞书私聊共享同一记忆
```

**典型工作流**：
- CLI 处理复杂任务（代码编写、文件操作）
- 飞书私聊用于移动场景（快速查询、状态检查）
- 两者记忆完全同步

### 场景 2：飞书为主，CLI 干预

你在飞书群聊中工作，但需要 CLI 终端的调试能力。

```bash
# 1. CLI 绑定到飞书群聊会话
> .bind cli oc_xxxxxxxxxxxxx

✅ CLI 已绑定到会话: oc_xxxxxxxxxxxxx

# 2. 现在 CLI 的输入使用飞书群聊的会话上下文
#    CLI 可以访问飞书群的全部对话历史
```

**典型工作流**：
- 飞书群聊是日常工作界面
- CLI 用于 `.stats`、`.status` 等诊断命令
- 通过 CLI 发送消息时，使用飞书群的记忆和文件

### 场景 3：解除绑定

```bash
# 解除 CLI 绑定
> .unbind cli

✅ CLI 已解除绑定（原: oc_xxxxxx）

# 解除飞书私聊绑定
> .unbind feishu ou_xxxxxxxxxxxxx

✅ 飞书私聊 (ou_xxxxxx...) 已解除绑定（原: default）

# 解除所有绑定
> .unbind all

✅ 已解除 2 个通道绑定
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `.bind status` | 查看所有通道绑定状态 |
| `.bind cli <会话>` | CLI 绑定到指定会话 |
| `.bind feishu <sender_id> <会话>` | 飞书私聊绑定到指定会话 |
| `.unbind cli` | 解除 CLI 绑定 |
| `.unbind feishu <sender_id>` | 解除飞书私聊绑定 |
| `.unbind all` | 解除所有绑定 |

**会话标识**：支持编号（`1`）和原始 ID（`default`、`oc_xxx`）。

## 内部实现

### ChannelRouter 核心数据结构

```python
class ChannelRouter:
    _bindings: dict[str, str]        # channel_id → primary_session_id
    _reverse: dict[str, list[str]]   # session_id → [channel_id, ...]
    _primary: str | None             # 当前主会话
```

**通道标识规范**：

| 通道 | 标识格式 | 示例 |
|------|----------|------|
| CLI | `__cli__` | `__cli__` |
| 飞书私聊 | `feishu_p2p:<sender_id>` | `feishu_p2p:ou_abc123` |
| 飞书群聊 | `feishu:<chat_id>` | `feishu:oc_xyz789` |

### 解析流程

```
CLI 输入:
  channel_router.resolve("__cli__")
  → 已绑定? 返回绑定的 session_id
  → 未绑定? 返回 "__cli__"（独立会话）

飞书消息:
  channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
  → chat_type == "p2p"?
    → 构造 "feishu_p2p:<sender_id>"，调用 resolve()
  → chat_type == "group"?
    → 返回 "feishu:<chat_id>"（群聊始终独立）
```

### 持久化

`ChannelRouter` 提供序列化方法（当前未启用自动持久化）：

```python
data = channel_router.to_dict()     # → {"bindings": ..., "reverse": ..., "primary": ...}
channel_router.from_dict(data)      # 恢复绑定状态
```

未来版本可能将绑定状态持久化到 `workspaces/bindings.json`。

## 与 Session 系统的关系

### 职责划分

| 系统 | 职责 |
|------|------|
| **SessionManager** | 会话的创建/销毁/切换/持久化；工作空间管理；工具注册表隔离 |
| **ChannelRouter** | 输入通道到 session_key 的映射；不涉及会话生命周期 |

### 数据流

```
用户输入 (CLI / 飞书)
    ↓
ChannelRouter.resolve() → session_key
    ↓
SessionManager.get_or_create(session_key) → Session + conversation_history
    ↓
UnifiedEngine.run_agent_with_thinking(session_key, history, ...)
```

### 关键约束

1. **群聊不参与绑定**：飞书群聊始终使用独立会话，这是设计约束而非 bug。
   群聊通常有多个参与者，绑定会导致混乱。

2. **绑定不创建会话**：`bind()` 只是映射关系，不会自动创建目标会话。
   如果目标会话不存在，首次消息会由 `SessionManager.get_or_create()` 自动创建。

3. **解绑不影响会话**：`unbind()` 仅移除映射，目标会话仍然存在，
   历史记忆不受影响。
