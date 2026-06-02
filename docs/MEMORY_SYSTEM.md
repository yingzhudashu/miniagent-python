# 三层记忆系统

> 模块: `miniagent/memory/` | 版本: 2.0.3

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
| **会话历史** | `workspaces/sessions/<safe_id>/history.json`，含 `user` / `thinking` / `assistant`；`thinking` 在调用 LLM 前由 `conversation_history_for_llm()` 映射为合法 `assistant` 文本块（映射时可按 `MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS` 截断，**不修改磁盘原文**） |
| **工具全文落盘** | `thinking` 中的工具输出依赖执行器回调 `on_tool_finish`；若直接调用 `run_agent()` 而未传入该回调，则不会写入工具全文块。`UnifiedEngine.run_agent_with_thinking` 已默认接线。 |
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
| `load(session_key)` | 加载会话记忆（先查 LRU 缓存，未命中则读磁盘） |
| `save(memory)` | 保存会话记忆到磁盘，同时更新 LRU 缓存 |
| `add_entry(session_key, entry)` | 添加记忆条目，自动落盘 + 更新关键词/嵌入索引 |
| `update_summary(session_key, summary, facts)` | 更新会话摘要，自动落盘 |
| `extract_facts(text)` | 从文本中提取关键事实 |
| `generate_turn_summary(user_input, tool_calls, reply)` | 生成单轮对话摘要 |
| `flush_keyword_index()` | 将挂起的关键词索引变更写入磁盘 |

### LRU 缓存

`DefaultMemoryStore._cache` 使用 `collections.OrderedDict` 实现 LRU 驱逐：

- **上限**：默认 50 会话，可通过 `MINIAGENT_MEMORY_STORE_CACHE_MAX` 环境变量覆盖
- **命中**：`move_to_end(session_id)` 提升活跃度
- **驱逐**：`popitem(last=False)` 移除最旧条目
- **写入**：`save()` 和 `load()`（未命中时）均经过 `_cache_put`，统一触发驱逐检查

此设计确保长期运行的进程不会因无限增长的缓存导致内存膨胀。

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
activity_log.log_session_start()   ← 首次读今日文件（后续 30 秒内走内存缓存）
activity_log.log_llm_call()        ← 每轮 LLM 调用
activity_log.log_tool_call()       ← 每次工具执行
activity_log.log_final_reply()     ← 最终回复
    ↓
memory/YYYY-MM-DD.md
```

### 读取缓存

`_read_today()` 有 **30 秒内存缓存**，避免每次 `log_session_start` 都读取 Growing 的 Markdown 文件。
缓存按文件路径隔离，跨日自动刷新。

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

Layer 3 包含两个互补的检索后端：关键词索引（始终启用）和嵌入搜索（环境变量控制）。
两者共享一个文本注册表以避免重复存储。

### 共享文本注册表

**位置**: `miniagent/memory/shared_registry.py`

关键词索引与嵌入搜索共用 `MemoryEntryRegistry`，避免重复存储 `user_snippet`、`summary`、`facts` 等文本字段：

- **存储结构**：以 `session_id:timestamp` 为键存储完整文本
- **引用模式**：两个索引只存储键，按需从注册表获取内容
- **内存节省**：约 50%（原每条记忆在两索引各存 ~500 字符 → 现仅存一份）
- **上限驱逐**：默认 3000 条（`MINIAGENT_REGISTRY_MAX_ENTRIES`），超限驱逐最早条目
- **持久化**：`workspaces/memory-registry.json`

### 关键词索引

**位置**: `miniagent/memory/keyword_index.py`

跨会话的长期记忆检索，使用关键词索引 + TF-IDF 加权。

#### 核心机制

1. **关键词提取**: 从每次对话中提取关键信息
   - 英文：按空格和标点分词，去停用词，过滤单字符
   - 中文：2-gram + 3-gram 字符组合
2. **倒排索引**: 建立 关键词 → [记忆条目] 的映射
3. **相关性排序**: 按匹配关键词数加权（3-gram 权重 1.5×）
4. **结果格式化**: 将检索到的记忆格式化为 Agent 可读的提示

#### 上限与驱逐

关键词索引有 **`max_entries`** 上限（默认 20000 关键词，`MINIAGENT_MEMORY_KEYWORD_INDEX_MAX`）。
超限时自动驱逐最早的关键词（基于插入顺序），避免索引无限增长。

#### API

| 函数 | 说明 |
|------|------|
| `search_relevant(query, top_k=8)` | 搜索相关记忆 |
| `format_search_results(results)` | 格式化为提示文本 |
| `get_index_stats()` | 获取索引统计 |
| `index_entry(session_id, entry)` | 索引一条记忆条目 |
| `prune_expired(days_old=30)` | 清理过期条目 |

### 嵌入搜索（可选）

**位置**: `miniagent/memory/embedding_search.py`

基于向量嵌入的语义搜索，使用余弦相似度计算相关性。

#### 配置

通过环境变量控制（默认关闭，仅使用关键词索引）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_EMBEDDING_ENABLED` | `0` | `1`/`true` 开启嵌入搜索 |
| `MINIAGENT_EMBEDDING_BASE_URL` | *(空)* | embedding 服务 URL |
| `MINIAGENT_EMBEDDING_MODEL` | *(空)* | embedding 模型 |
| `MINIAGENT_EMBEDDING_API_KEY` | *(空)* | embedding API 密钥 |
| `MINIAGENT_EMBEDDING_DIMENSION` | `1536` | 向量维度 |
| `MINIAGENT_EMBEDDING_TOP_K` | `8` | 最多返回条目数 |
| `MINIAGENT_EMBEDDING_MIN_SCORE` | `0.3` | 最低余弦相似度阈值 |
| `MINIAGENT_EMBEDDING_MAX_ENTRIES` | `2000` | 嵌入索引上限 |

#### 存储与驱逐

- 索引文件：`<state_dir>/embedding-index.json`
- 每条记忆缓存其 1536 维向量（约 12KB/条）
- **上限**：2000 条（约 24MB），超限驱逐最早条目
- 使用内容 hash 检测重复，相同内容不重复索引

#### 检索流程

执行阶段（`execute_plan`）会先尝试嵌入搜索，不足 5 条时补充关键词索引：

```
用户输入 → 嵌入搜索（若启用）→ 不足 5 条 → 关键词索引补充 → 格式化注入 system prompt
```

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
| `max_turns` | 400 | ReAct 最大轮数（与 `AGENT_MAX_TURNS` / `AgentConfig` 一致；可由环境变量覆盖） |
| 记忆检索 top_k | 8 | Layer 3 返回条目数 |

## 环境变量汇总

| 变量 | 默认值 | 影响模块 |
|------|--------|----------|
| `MINIAGENT_MEMORY_STORE_CACHE_MAX` | `50` | `store.py` LRU 缓存上限（会话数） |
| `MINIAGENT_REGISTRY_MAX_ENTRIES` | `3000` | `shared_registry.py` 共享注册表上限 |
| `MINIAGENT_MEMORY_KEYWORD_INDEX_MAX` | `20000` | `keyword_index.py` 关键词数上限 |
| `MINIAGENT_EMBEDDING_ENABLED` | `0` | `embedding_search.py` 是否启用嵌入搜索 |
| `MINIAGENT_EMBEDDING_MAX_ENTRIES` | `2000` | `embedding_search.py` 嵌入条目上限 |
| `MINI_AGENT_DREAM_*` | `7d/30d/365d` | `dream_scheduler.py` 维护周期 |
| `MINI_AGENT_DREAM_SIZE_BYTES` | *(无)* | 体量闸门阈值 |
| `MINI_AGENT_HISTORY_TAIL_MESSAGES` | `200` | 历史保留消息数 |
