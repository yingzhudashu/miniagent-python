# 三层记忆系统

> 模块: `miniagent/memory/` | 版本: 2.0.1

## 架构概览

Mini Agent 采用三层记忆架构，确保 Agent 既能记住近期对话，又能从长期经验中学习。

**运行时注入（当前版本）**：`memory_store`、`activity_log` 与关键词索引实例由入口构造并放入 [`RuntimeContext`](../miniagent/runtime/context.py)；执行路径（`UnifiedEngine` / `execute_plan`）优先使用注入实例。未注入时回落到 **单一进程默认 bundle**（[`miniagent/memory/defaults.py`](../miniagent/memory/defaults.py) 的 `get_process_default_memory_bundle()`），根目录与 `unified_entry` 一致（`MINI_AGENT_STATE`，默认 `<cwd>/workspaces`）。自 **2.0.0** 起不再提供包级惰性别名 `miniagent.memory.memory_store` / `activity_log` 等；请使用 `get_process_default_memory_bundle()`、`resolve_memory_dependencies()` 或仅依赖 `RuntimeContext` 注入（见 [`ARCHITECTURE.md`](ARCHITECTURE.md)）。

```
┌─────────────────────────────────────────────────┐
│               Agent 执行上下文                     │
│                                                  │
│  Layer 1: 短期记忆 (Session Memory)               │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/store.py                       │  │
│  │ - DefaultMemoryStore（RuntimeContext 注入）    │  │
│  │ - load/save session 记忆                   │  │
│  │ - extract_facts() 事实提取                  │  │
│  │ - generate_turn_summary() 轮次摘要          │  │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  Layer 2: 活动日志 (Activity Log)                 │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/activity_log.py                │  │
│  │ - ActivityLogger（RuntimeContext 注入）      │  │
│  │ - log_session_start()                     │  │
│  │ - log_llm_call()                          │  │
│  │ - log_tool_call()                         │  │
│  │ - log_final_reply()                       │  │
│  │ 写入: memory/YYYY-MM-DD.md                 │  │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  Layer 3: 语义检索 (Semantic Memory)              │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/keyword_index.py               │  │
│  │ - 关键词索引 + TF-IDF 加权                  │  │
│  │ - search_relevant_memory()（支持注入索引实例）│  │
│  │ - format_search_results()                 │  │
│  │ - get_index_stats()                       │  │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  上下文管理                                      │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/context.py                     │  │
│  │ - DefaultContextManager                   │  │
│  │ - Token 计数 + 自动压缩                    │  │
│  │ - 消息窗口管理                             │  │
│  │ - inject_memory() 注入检索结果             │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 会话历史、按会话日记与分层精炼

与上图中的「结构化会话记忆（`DefaultMemoryStore`）」并行，另有**磁盘上的对话轨迹与渐进式披露**管线（实现见 `miniagent/memory/history_bridge.py`、`history_archive.py`、`layered_memory.py`、`memory_pipeline.py`、`dream_scheduler.py`）：

| 组件 | 路径 / 行为 |
|------|-------------|
| **会话历史** | `workspaces/sessions/<safe_id>/history.json`，含 `user` / `thinking` / `assistant`；`thinking` 在调用 LLM 前由 `conversation_history_for_llm()` 映射为合法 `assistant` 文本块 |
| **会话日记（归档）** | 超长历史按**完整轮**剪切到 `workspaces/memory/diary/<safe_session_id>/YYYY-MM-DD.md`（JSON 块原样保存），并在历史中插入 `_history_archive_marker` 衔接说明 |
| **会话级长期索引** | `workspaces/memory/session_lt/<safe_session_id>.json`（日摘要与日记路径占位，由 `dream_scheduler` 维护） |
| **Agent 级长期记忆** | `workspaces/memory/agent_lt/global.json` |
| **Dream 式维护** | `workspaces/memory/dream_state.json` + `dream.lock`；周期默认 7d / 30d / 365d（环境变量 `MINI_AGENT_DREAM_*`），体量超 `MINI_AGENT_DREAM_SIZE_BYTES` 时可跳过周期闸门 |

全局 **Activity Log**（`memory/YYYY-MM-DD.md`）仍保留，与会话日记互补：前者偏运维流水，后者按会话隔离存放从 `history` 迁出的原文块。

## Layer 1: 短期记忆 (Session Memory)

**位置**: `miniagent/memory/store.py`（`workspaces/memory/<safe_session_key>.json` 的摘要与条目）

每个会话（session）独立存储的**结构化**记忆层（与 `sessions/` 下的 `history.json` 对话轨迹不同），包含：

### 存储结构

```
workspaces/sessions/<safe_session_id>/   # 注意：目录名为文件名安全化后的 session_key
├── history.json          # 当前对话历史（可含 thinking / 归档锚点）
├── history_snapshots/    # 历史快照（每次会话保存）
│   └── 0001_<timestamp>.json
├── files/                # 会话相关文件
└── skills/               # 会话专属技能

workspaces/memory/diary/<safe_session_id>/   # 从 history 剪切出的原文归档（按日 .md）
workspaces/memory/session_lt/               # 会话级长期索引 JSON
workspaces/memory/agent_lt/                 # Agent 级长期记忆 JSON
```

### 核心功能

| 函数 | 说明 |
|------|------|
| `load(session_key)` | 加载会话记忆 |
| `add_entry(session_key, entry)` | 添加记忆条目 |
| `update_summary(session_key, summary, facts)` | 更新会话摘要 |
| `extract_facts(text)` | 从文本中提取关键事实 |
| `generate_turn_summary(user_input, tool_calls, reply)` | 生成单轮对话摘要 |
| `search(session_key, query)` | 在当前会话记忆中搜索 |

### 记忆条目格式

```json
{
  "timestamp": "2026-05-09T12:00:00+08:00",
  "user_snippet": "用户的原始输入（前100字）...",
  "summary": "本轮对话的摘要",
  "facts": ["提取的事实1", "提取的事实2"]
}
```

## Layer 2: 活动日志 (Activity Log)

**位置**: `miniagent/memory/activity_log.py`

详细的操作流水账，写入 `memory/YYYY-MM-DD.md`。

### 记录内容

| 事件类型 | 记录字段 |
|---------|---------|
| 会话开始 | session_key, 用户输入, 来源(cli/feishu) |
| LLM 调用 | 轮次, 模型, 消息数, 工具数, 思考内容, token 用量 |
| 工具调用 | 工具名, 意图, 参数, 结果, 耗时, 成功/失败 |
| 最终回复 | session_key, 回复内容 |

### 写入流程

```
Executor (ReAct循环)
    ↓
activity_log.log_session_start()
activity_log.log_llm_call()      ← 每轮 LLM 调用
activity_log.log_tool_call()     ← 每次工具执行
activity_log.log_final_reply()   ← 最终回复
    ↓
memory/YYYY-MM-DD.md
```

### 示例输出

```markdown
## [12:30:15] 会话开始 - default
**用户**: 帮我检查一下今天的天气

## [12:30:16] LLM 调用 (第1轮)
**模型**: gpt-4o-mini | **Token**: 150 → 80
**工具数**: 5 | **思考**: 用户需要查询天气...

## [12:30:17] 工具调用: web_search
**意图**: 搜索北京天气 | **耗时**: 230ms | ✅ 成功
```

## Layer 3: 语义检索 (Semantic Memory)

**位置**: `miniagent/memory/keyword_index.py`

跨会话的长期记忆检索，使用关键词索引 + TF-IDF 加权。

### 核心机制

1. **关键词提取**: 从每次对话中提取关键信息
2. **TF-IDF 加权**: 词频-逆文档频率评分
3. **相似度搜索**: 根据当前输入检索相关历史记忆
4. **结果格式化**: 将检索到的记忆格式化为 Agent 可读的提示

### API

| 函数 | 说明 |
|------|------|
| `search_relevant_memory(query, top_k=8)` | 搜索相关记忆 |
| `format_search_results(results)` | 格式化为提示文本 |
| `get_index_stats()` | 获取索引统计 |
| `add_entry(text, metadata)` | 添加索引条目 |

## 上下文管理

**位置**: `miniagent/memory/context.py`

### DefaultContextManager

管理 LLM 对话上下文，确保在 token 限制内高效运行。

| 功能 | 说明 |
|------|------|
| Token 计数 | 实时跟踪当前上下文 token 用量 |
| 自动压缩 | 超过阈值时压缩上下文（移除旧消息） |
| 记忆注入 | 将检索到的记忆注入到 system prompt |
| 消息窗口 | 维护最近的 N 条消息，丢弃过旧消息 |
| 工具 Schema | 管理可用工具列表的上下文表示 |

### Token 压缩策略

```
messages = [
  system_prompt,        ← 始终保留
  memory_injection,     ← 始终保留
  recent_messages...,   ← 压缩时保留
  older_messages...,    ← 压缩时优先移除
]
```

## 记忆注入到 Agent 执行流程

```
用户输入
    ↓
1. Layer 3 语义检索 → 搜索相关历史记忆
    ↓
2. Layer 1 加载 → 读取当前会话记忆
    ↓
3. 上下文管理 → 构建完整的上下文窗口
    ↓
4. 记忆注入 → 将检索结果注入 system prompt
    ↓
5. LLM 调用 → 带着完整上下文生成回复
    ↓
6. Layer 2 记录 → 写入活动日志
    ↓
7. Layer 1 更新 → 更新会话记忆 + 事实提取
```

## 配置

记忆系统在 `miniagent/core/config.py` 中可配置：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `context_compress_threshold` | 0.8 | token 压缩阈值 (80% 窗口) |
| `max_turns` | 10 | ReAct 最大轮数 |
| 记忆检索 top_k | 8 | Layer 3 返回条目数 |
