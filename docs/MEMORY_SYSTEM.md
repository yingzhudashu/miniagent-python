# 三层记忆系统

> Mini Agent Python | 版本: 2.1.0 | 最后更新: 2026-07-12 | 与 `miniagent.__version__` 对齐 | 模块: `miniagent/memory/`

## 架构概览

Mini Agent 采用三层记忆架构，确保 Agent 既能记住近期对话，又能从长期经验中学习。

> 下文路径简写 `workspaces/...` 表示 `{paths.state_dir}/...`（canonical：`{miniagent}/workspaces/projects/{project_key}/...`，见 [ENGINEERING.md](ENGINEERING.md) §3）。

**运行时注入**：正式入口通过 [`miniagent/memory/runtime.py`](../miniagent/memory/runtime.py) 构造唯一 `MemoryRuntime`，其中共享同一个注册表、关键词索引、嵌入索引、存储、活动日志和上下文服务；[`ApplicationContainer.memory`](../miniagent/bootstrap/application.py) 统一持有它，`UnifiedEngine` / `run_agent` / `execute_plan` 只接受显式注入，不存在模块级默认 bundle。状态根由 `MINIAGENT_PATHS_STATE_DIR` 或 `resolve_project_state_dir()` 决定（默认 `{miniagent}/workspaces/projects/{project_key}/`）。

```
┌─────────────────────────────────────────────────┐
│               Agent 执行上下文                     │
│                                                  │
│  Layer 1: 短期记忆 (Session Memory)               │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/store.py                   │  │
│  │ - DefaultMemoryStore（ApplicationContainer 注入） │  │
│  │ - load/save session 记忆                   │  │
│  │ - extract_facts() 事实提取                  │  │
│  │ - generate_turn_summary() 轮次摘要          │  │
│  └───────────────────────────────────────────┘  │
│                                                  │
│  Layer 2: 活动日志 (Activity Log)                 │
│  ┌───────────────────────────────────────────┐  │
│  │ miniagent/memory/activity_log.py                │  │
│  │ - ActivityLogger（ApplicationContainer 注入） │  │
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
│  │ - stable system / history / current user 分层管理 │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 会话历史、按会话日记与分层精炼

与上图中的「结构化会话记忆（`DefaultMemoryStore`）」并行，另有**磁盘上的对话轨迹与渐进式披露**管线（实现见 `miniagent/memory/history_bridge.py`、`history_archive.py`、`layered_memory.py`、`memory_pipeline.py`、`dream_scheduler.py`）：

| 组件 | 路径 / 行为 |
|------|-------------|
| **会话历史** | `{paths.state_dir}/sessions/<safe_id>/history.json`（默认 `{miniagent}/workspaces/projects/{project_key}/sessions/…`，见 [ENGINEERING.md](ENGINEERING.md) §3），含 `user` / `thinking` / `assistant`；`thinking` 在调用 LLM 前由 `conversation_history_for_llm()` 映射为合法 `assistant` 文本块。默认 `memory.thinking_for_llm_mode=compact`，按 `memory.thinking_for_llm_compact_max_chars` 截断摘要；`full` 模式才使用 `memory.thinking_for_llm_max_chars`，且**不修改磁盘原文**。 |
| **工具全文落盘** | `thinking` 中的工具输出依赖执行器回调 `on_tool_finish`；若直接调用 `run_agent()` 而未传入该回调，则不会写入工具全文块。`UnifiedEngine.run_agent_with_thinking` 已默认接线。 |
| **会话日记（归档）** | `{paths.state_dir}/memory/diary/<safe_session_id>/YYYY-MM-DD.md`（JSON 块原样保存），并在历史中插入 `_history_archive_marker` 衔接说明 |
| **会话级长期索引** | `{paths.state_dir}/memory/session_lt/<safe_session_id>.json`（日摘要与日记路径占位，由 `dream_scheduler` 维护） |
| **Agent 级长期记忆** | `{paths.state_dir}/memory/agent_lt/global.json` |
| **Dream 式维护** | `{paths.state_dir}/memory/dream_state.json` + `dream.lock`；周期默认 7d / 30d / 365d（JSON 配置 `dream.diary_refine_sec` / `dream.session_lt_refine_sec` / `dream.agent_lt_refine_sec`），体量超 `dream.size_force_bytes` 时可跳过周期闸门 |

全局 **Activity Log**（`memory/YYYY-MM-DD.md`）仍保留，与会话日记互补：前者偏运维流水，后者按会话隔离存放从 `history` 迁出的原文块。

## Layer 1: 短期记忆 (Session Memory)

**位置**: `miniagent/memory/store.py`（`{paths.state_dir}/memory/<safe_session_key>.json` 的摘要与条目）

每个会话（session）独立存储的**结构化**记忆层（与 `sessions/` 下的 `history.json` 对话轨迹不同），包含：

- `cumulative_summary`：会话累计摘要。
- `key_facts`：便于提示词直接消费的字符串事实摘要。
- `ground_truth_facts`：可追溯、可更新、可纠正的长期确定事实，是需求自澄清优先使用的 solid ground truth。
- `entries`：最近对话条目。
- `uploaded_files`：上传文件元数据。

### 存储结构

```
{paths.state_dir}/sessions/<safe_session_id>/   # canonical；默认见 [ENGINEERING.md](ENGINEERING.md) §3
├── history.json          # 当前对话历史（可含 thinking / 归档锚点）
├── history_snapshots/    # 历史快照（每次会话保存）
│   └── 0001_<timestamp>.json
├── files/                # 会话相关文件
└── skills/               # 会话专属技能

{paths.state_dir}/memory/diary/<safe_session_id>/   # 从 history 剪切出的原文归档（按日 .md）
{paths.state_dir}/memory/session_lt/               # 会话级长期索引 JSON
{paths.state_dir}/memory/agent_lt/                 # Agent 级长期记忆 JSON
```

### 核心功能

| 函数 | 说明 |
|------|------|
| `load(session_key)` | 加载会话记忆（先查 LRU 缓存，未命中则读磁盘） |
| `save(memory)` | 保存会话记忆到磁盘，同时更新 LRU 缓存 |
| `add_entry(session_key, entry)` | 添加记忆条目，自动落盘 + 更新关键词/嵌入索引 |
| `update_summary(session_key, summary, facts)` | 更新会话摘要，自动落盘 |
| `extract_facts(text)` | 从文本中提取关键事实 |
| `ground_truth.apply_ground_truth_updates(memory, text)` | 提取稳定事实并更新 `ground_truth_facts` |
| `generate_turn_summary(user_input, tool_calls, reply)` | 生成单轮对话摘要 |
| `flush_keyword_index()` | 将挂起的关键词索引变更写入磁盘 |

### LRU 缓存

`DefaultMemoryStore._cache` 使用 `collections.OrderedDict` 实现 LRU 驱逐：

- **上限**：默认 200 会话，可通过 `memory.store_cache_max` JSON 配置调整
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

### Solid Ground Truth 事实

`ground_truth_facts` 用于保存可作为后续需求澄清依据的长期稳定事实。它与 `key_facts` 的区别是：`key_facts` 是面向提示词的摘要字符串，`ground_truth_facts` 是带 key、状态、置信度和证据的结构化事实。

```json
{
  "key": "output.language",
  "value": "默认用中文回答",
  "category": "output_format",
  "confidence": 0.95,
  "source": "user",
  "status": "active",
  "created_at": "2026-06-07T00:00:00+00:00",
  "updated_at": "2026-06-07T00:00:00+00:00",
  "supersedes": null,
  "evidence": "记住以后回复都用中文"
}
```

写入规则保持保守：

- 只持久化稳定偏好、项目约束、环境配置、身份/工作流偏好、输出格式偏好。
- “这次”“本次”“临时”“一次性”等临时任务信息不提升为 ground truth。
- 同一 `key` 出现新值时，旧 active 事实标记为 `superseded`，新事实标记为 `active`，并在 `supersedes` 中保留旧值。
- 需求澄清优先使用 active 且高置信的 ground truth；低置信、冲突或已 superseded 的事实不会静默替用户决定。

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

Layer 3 包含两个互补的检索后端：关键词索引（始终启用）和嵌入搜索（由 `embedding.*` JSON 配置控制）。
两者共享一个文本注册表以避免重复存储。

### 共享文本注册表

**位置**: `miniagent/memory/shared_registry.py`

关键词索引与嵌入搜索共用 `MemoryEntryRegistry`，避免重复存储 `user_snippet`、`summary`、`facts` 等文本字段：

- **存储结构**：以 `session_id:timestamp` 为键存储完整文本
- **引用模式**：两个索引只存储键，按需从注册表获取内容
- **内存节省**：约 50%（原每条记忆在两索引各存 ~500 字符 → 现仅存一份）
- **上限驱逐**：默认 3000 条（`memory.registry_max_entries`），超限驱逐最早条目
- **持久化**：`{paths.state_dir}/memory-registry.json`

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

关键词索引有 **`max_entries`** 上限（默认 20000 关键词，`memory.keyword_index_max`）。
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

通过包内 defaults → `config.user.json` 的 `embedding.*` 控制（默认关闭，仅使用关键词索引）：

| JSON 路径 | 默认值 | 说明 |
|------|--------|------|
| `embedding.enabled` | `false` | 开启嵌入搜索 |
| `embedding.base_url` | `null` | OpenAI-compatible embedding 服务 URL |
| `embedding.model` | `null` | embedding 模型 |
| `secrets.openai_api_key` | `null` | embedding 调用使用的 API key |
| `embedding.dimension` | `1536` | 向量维度 |
| `embedding.top_k` | `8` | 最多返回条目数 |
| `embedding.min_score` | `0.3` | 最低余弦相似度阈值 |
| `embedding.max_entries` | `2000` | 嵌入索引上限 |
| `embedding.cache_max_size` | `1000` | embedding API 调用缓存条数 |
| `embedding.cache_ttl_seconds` | `3600` | embedding API 调用缓存 TTL |

#### 存储与驱逐

- 索引文件：`<state_dir>/embedding-index.json`
- 每条记忆缓存其 1536 维向量（约 12KB/条）
- **上限**：`embedding.max_entries` 条（默认 2000，约 24MB），超限驱逐最早条目
- 使用内容 hash 检测重复，相同内容不重复索引

#### 检索流程

执行阶段（`execute_plan`）会先尝试嵌入搜索，不足 5 条时补充关键词索引：

```
用户输入 → 嵌入搜索（若启用）→ 不足 5 条 → 关键词索引补充 → 格式化 → current turn user context
```

## 上下文管理

**位置**: `miniagent/memory/context.py`

### DefaultContextManager

管理 LLM 对话上下文，确保在 token 限制内高效运行。

| 功能 | 说明 |
|------|------|
| Token 计数 | 实时跟踪当前上下文 token 用量 |
| 自动压缩 | 超过阈值时压缩上下文（移除旧消息） |
| 本轮记忆上下文 | 将结构化会话记忆和检索结果放入 current turn user context |
| 消息窗口 | 维护最近的 N 条消息，丢弃过旧消息 |
| 工具 Schema | 管理可用工具列表的上下文表示 |

### Token 压缩策略

```
messages = [
  stable_system_prompt,      ← 第一条 system，尽量稳定
  history...,                ← 历史消息，可含 compact thinking 摘要
  current_turn_user_context, ← 本轮动态记忆、KB、时间、文件根目录等
  recent_messages...,        ← ReAct 过程中追加并参与窗口管理
  older_messages...,         ← 压缩时优先移除
]
```

## 记忆进入 Agent 执行流程

```
用户输入
    ↓
1. Layer 3 语义检索 → 搜索相关历史记忆
    ↓
2. Layer 1 加载 → 读取当前会话记忆
    ↓
3. 当前轮上下文 → 合并结构化会话记忆、keyword_context、kb_context
    ↓
4. Prompt 分层 → stable system + history + current turn user context
    ↓
5. LLM 调用 → 带着完整上下文生成回复
    ↓
6. Layer 2 记录 → 写入活动日志
    ↓
7. Layer 1 更新 → 更新会话记忆 + 事实提取
```

## 非 LLM 能力清单

以下能力**不调用 LLM**，采用启发式、占位或确定性规则实现。阅读文档或排查「记忆质量」问题时，请先区分它们与真正的语义摘要/精炼：

| 能力 | 模块 / 函数 | 实际行为 | 常见误解 |
|------|-------------|----------|----------|
| 回合事实提取 | `store.extract_facts()` | 正则启发式 | 并非语义理解，复杂事实可能漏提或误提 |
| 回合摘要 | `store.generate_turn_summary()` | 字符串拼接（用户意图 + 工具名 + 回复截断） | 并非 LLM 摘要 |
| 上下文压缩 | `context.DefaultContextManager.compress()` | 中间历史压成一行占位说明 | 并非 LLM 摘要 |
| Token 估算 | `context.estimate_tokens*` | 中英文字符启发式 | 与真实 tokenizer 有偏差，仅用于预算 |
| 中文关键词 | `keyword_index.extract_keywords()` | n-gram + 英文分词，无 jieba | 检索精度有限 |
| Dream 维护 | `dream_scheduler._refine_session()` | 登记日记索引占位、截断 `session_lt` / `agent_lt` 列表 | **不是** LLM 夜间精炼；日摘要字段为占位文本 |
| 确定事实 | `ground_truth.extract_ground_truth_facts()` | 保守正则 + 纠正句式 | 只提升稳定偏好/约束，一次性任务细节不入库 |
| 嵌入检索 | `embedding_search` | 需 `embedding.enabled` 与 API；未配置时回退关键词 | 配置缺失时静默降级，不会报错 |
| 跨会话检索 | `DefaultMemorySearch.search_relevant_memory()` | 默认忽略 `session_key`，全索引检索 | 非按会话隔离检索 |

**有 LLM 参与的路径**（不在本包内）：规划、ReAct 回复、部分引擎侧任务分类等；本包只负责**持久化、检索、格式化与窗口管理**。

## 配置

记忆相关参数在 `config.user.json` 的 `memory` / `agent` / `embedding` 节配置（默认值见 [`miniagent/resources/config.defaults.json`](../miniagent/resources/config.defaults.json)）：

| JSON 路径 | 默认值 | 说明 |
|-----------|--------|------|
| `agent.context_compress_threshold` | 0.6 | token 压缩阈值（占上下文窗口比例） |
| `agent.max_turns` | 400 | ReAct 最大轮数 |
| `embedding.top_k` | 8 | Layer 3 嵌入检索返回条目数 |
| `memory.history_tail_messages` | 200 | 保留的历史消息 tail 长度 |
| `memory.store_cache_max` | 200 | 短期记忆 LRU 会话数上限 |
| `memory.thinking_for_llm_mode` | `compact` | `thinking` 历史回灌模式：`off` / `compact` / `full` |
| `memory.thinking_for_llm_compact_max_chars` | 1200 | `compact` 模式下 thinking 摘要最大字符数 |
| `memory.thinking_for_llm_max_chars` | 10000 | `full` 模式下 thinking 正文最大字符数 |

## 环境变量汇总

> 用户配置仅通过 JSON（`config.user.json` > 包内 defaults）。**运维 / 路径类 env 仍有效**（如 `MINIAGENT_PATHS_STATE_DIR`、`AGENT_DEBUG`），完整分类见 [ENGINEERING.md §1.2](ENGINEERING.md#12-环境变量分类)。
>
> **迁移示例**（历史 `MINIAGENT_*` 配置键 → JSON）：
> - `MINIAGENT_MEMORY_STORE_CACHE_MAX` → `memory.store_cache_max`
> - `MINIAGENT_REGISTRY_MAX_ENTRIES` → `memory.registry_max_entries`
> - `MINIAGENT_MEMORY_KEYWORD_INDEX_MAX` → `memory.keyword_index_max`
>
> 完整对照见 `miniagent/resources/config.defaults.json` 的 `memory`、`embedding`、`dream` 节。
