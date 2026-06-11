# 性能测试与优化

> Mini Agent Python | 与 `miniagent.__version__` 对齐 | 补充 [ENGINEERING.md](ENGINEERING.md)

本文说明如何度量 **进程内** 开销（CPU、内存、本地 I/O），如何与 **端到端（含 LLM 网络）** 指标区分，以及如何维护基线与可选 CI。

## 1. KPI 分层

| 层级 | 指标 | 说明 |
|------|------|------|
| **L1 本地** | `wall_local`（秒）、`alloc_delta`（字节，可选 tracemalloc）、**关键词索引写盘次数**、上下文 `json.dumps` 相关耗时 | 使用 **Mock LLM**，不访问外网；见 `tests/test_perf_synthetic.py` |
| **L2 剖析** | cProfile 累计时间、py-spy 火焰图、RSS | 开发机按需运行，见 §3 |
| **L3 端到端** | 单用例 wall time、p50/p95、token usage、错误率、trace 写入开销 | 依赖 API，必须显式设置 `MINIAGENT_REAL_API_STRESS=1`；见 `tests/evaluation/`、[docs/ENGINEERING.md](ENGINEERING.md) §5 |

**注意**：线上感知的 p95 延迟通常由 **LLM/HTTP** 主导；L1 回归用于防止 Python 侧退化，不替代端到端评测。

### 1.1 进程关闭顺序（`shutdown_runtime`）

与 [miniagent/engine/shutdown.py](../miniagent/engine/shutdown.py) 实现一致，供排障与代码审阅对齐：

1. 取消并等待 `RuntimeContext.shutdown_tracked_tasks`（如 `tick_once` 派生的 job）。
2. `cancel_pending_dream_tasks()`（记忆维护后台）。
3. 定时任务 ticker：`stop_event.set()`，取消并 `await` ticker task。
4. 飞书：`await feishu.stop_async()`（或 fallback await），再防御性 `reset_feishu_ws_singleton()`。
5. 可选：`message_queue.abort_all_chats()`。
6. `cleanup_all_processes()`；按需 `release_session_lock`；按需 `unregister_instance()`。
7. 可选：`loop.shutdown_default_executor()`（短超时）。**信号路径**（`SIGINT`/`SIGTERM`）当前在 [miniagent/engine/main.py](../miniagent/engine/main.py) 传入 `shutdown_default_executor=False`，以降低与全屏 CLI / 线程池的竞态。

**与 `run_cli_loop` 的关系**：用户正常 `quit` 时，循环末尾通常会先 `release_session_lock` + `unregister_instance()`，随后 `unified_main` 再调用 `shutdown_runtime(..., release_cli_session_lock=False, call_unregister=False)`，避免重复；`/stop` 与信号路径则传 `True` 以覆盖未走循环清理即退出的情况。

## 2. 场景矩阵（合成）

| ID | 场景 | 目的 |
|----|------|------|
| S1 | `execute_plan` + Mock 流式、1 次工具调用 | 执行器主路径本地耗时上界（宽松断言） |
| S2 | `DefaultMemoryStore.add_entry` 多次 + 单次 `flush_keyword_index` | 关键词索引 **合并落盘**（相对「每 add 一次 save」） |
| S3 | `DefaultContextManager` + 较多工具 schema 的 token 估算 | 上下文/序列化路径冒烟（阈值宽松） |
| S4 | 多工具下反复 `get_token_report`（burst） | 预算与报告路径；依赖工具 schema token **缓存**，防 `needs_compression` 相关路径退化 |
| S5 | `_normalize_lark_md` 大段正文 | 飞书 **纯 CPU** 规范化；不访问网络，与 `poll_server` 侧热点对照 |
| S6 | 批量 `add_entry` + `flush` 的 tracemalloc 峰值 | 分配量宽松上界；与 `scripts/perf_profile_tracemalloc.py` 场景一致 |
| S7 | `serialize_exec_payload_sample`（DefaultContextManager + messages/tools `json.dumps`） | 与 `execute_plan` 请求组装对齐的 Python 序列化冒烟 |
| S8 | 连续加载 260 个会话到 `DefaultMemoryStore` | 验证 LRU cache 驱逐（`memory.store_cache_max` 默认 200） |
| S9 | `EmbeddingIndex` 连续添加 250 条目 | 验证 `max_entries` 上限驱逐（200） |
| S10 | `KeywordIndex` 连续添加 200 条目 | 验证关键词数不超过 `max_entries`（50） |

扩展场景时保持 **确定性**（固定 tmp 状态目录、Mock client），避免在默认 CI 中依赖网络。可选在 CI 中设置 **`PYTHONHASHSEED=0`**（见 `.github/workflows/perf-smoke.yml`）以降低 dict 迭代顺序带来的抖动。

## 3. 本地剖析命令

### 3.1 cProfile（CPU）

对 `scripts/perf_profile_tracemalloc.py` **单次进程**跑 cProfile 时，累计时间常被 **importlib 冷启动** 主导；要突出内存批处理热路径，请加大 `--inner-repeat`（每次迭代使用独立子目录，避免状态无限膨胀）：

```bash
python -m cProfile -o perf.out scripts/perf_profile_tracemalloc.py --no-tracemalloc --inner-repeat 80
python -c "import pstats; p=pstats.Stats('perf.out'); p.strip_dirs().sort_stats('cumtime').print_stats(40)"
```

### 3.2 tracemalloc（分配热点）

```bash
python scripts/perf_profile_tracemalloc.py --top 25
python scripts/perf_profile_tracemalloc.py --json-out perf-snapshot.json
python scripts/perf_profile_tracemalloc.py --inner-repeat 20 --json-out perf-snapshot.json
```

### 3.3 py-spy（采样，需单独安装）

```bash
py-spy record -o profile.svg -- python scripts/perf_profile_tracemalloc.py --no-tracemalloc --inner-repeat 40
```

### 3.4 两次剖析 JSON 对比（基线）

`perf_profile_tracemalloc.py --json-out` 写入的 JSON 含 `tracemalloc_peak_mib`、`inner_repeat` 等字段。将一次输出保存为 `tests/perf_baselines/<你的环境>.json` 后，可与新跑结果对比（**非门禁**，用于人工或可选告警）：

```bash
python scripts/compare_perf_snapshots.py tests/perf_baselines/my-baseline.json perf-snapshot.json --warn-ratio 1.35
```

对比 **tracemalloc_peak_mib** 时，请使两侧 JSON 的 **`inner_repeat` 与是否 `--no-tracemalloc`** 一致；否则脚本会打印 WARN，峰值可能不可比。`compare_perf_snapshots.py` 仅接受根为 **JSON 对象** 的文件（与 `perf_profile` 输出一致），勿将 `example.json` 的 `scenarios` 数组根文件误作输入。

### 3.5 真实 API 压测（显式门禁）

真实 API 压测默认不会运行；即使存在 API key，也必须显式打开门禁，避免 CI 或本地全量测试意外产生费用：

```bash
set MINIAGENT_REAL_API_STRESS=1
set MINIAGENT_REAL_API_PERF_DIR=workspaces/logs/perf
python -m pytest tests/evaluation/test_perf_real_api.py -v -s
```

压测使用当前 OpenAI-compatible 配置（`model.base_url`、`model.model`、`OPENAI_API_KEY`）。产物默认写入 `workspaces/logs/perf/`，属于过程性文件，不提交到仓库。Trace 内容策略为 `metrics_only`：只记录模型、耗时、token、状态、会话/请求关联 ID、错误类型等指标，不记录完整 prompt、response 或密钥。

## 4. 基线文件格式（`tests/perf_baselines/`）

用于人工或离线对比（**勿提交密钥**）。基线文件位于 `tests/perf_baselines/` 目录；首次使用请运行 `mkdir -p tests/perf_baselines` 创建。

字段建议：

- `schema_version`：整数，格式变更时递增
- `commit`：可选，Git SHA
- `generated_at`：ISO8601 UTC
- `environment`：`python`, `platform`
- `scenarios`：数组，元素含 `id`, `median_ms`, `notes`（与 L1 合成用例 id 对齐，便于 PR 描述引用）

另可将 `scripts/perf_profile_tracemalloc.py --json-out` 的产出 **复制** 为 `perf_baselines/tracemalloc_*.json`，供 `compare_perf_snapshots.py` 使用（该脚本读取的是脚本 JSON 格式，与 `example.json` 的 `scenarios` 数组可并存为不同文件）。

CI **不**依赖基线文件是否存在；可选 workflow 仅上传当次脚本输出 artifact。

## 5. 已确认/已缓解的热点（代码侧）

### 5.1 已缓解

- **关键词索引**：`KeywordIndex` 使用 `_dirty`；`index_entry` 只改内存。`DefaultMemoryStore.add_entry` 不再每次 `save()`；在 [`miniagent/core/executor.py`](../miniagent/core/executor.py) 的 `_save_session_memory` 末尾 **`flush_keyword_index()`** 保证每轮仍落盘一次。批量 `add_entry` 后显式 `flush_keyword_index()` 可减少写次数。进程退出时 [`miniagent/memory/defaults.py`](../miniagent/memory/defaults.py) 通过 **atexit** 再刷一次默认 bundle 的索引，降低异常退出丢失概率。关键词索引有 **`max_entries`** 上限（默认 20000，`memory.keyword_index_max`），超过时修剪最早关键词。
- **上下文预算中的工具 schema**：[`miniagent/memory/context.py`](../miniagent/memory/context.py) 的 `DefaultContextManager` 对 `estimate_tool_tokens`（内部多次 `json.dumps(tool)`）做 **按次失效缓存**（调用 **`set_tools`** 或构造后首次用时计算；之后复用）。若需更新工具列表或 schema 内容，**必须**通过 `set_tools` 传入新列表，勿仅原地修改已绑定列表并依赖预算立即变化。
- **Prompt cache 友好分层**：执行阶段请求固定为 `stable system -> history -> current turn user context`。Agent 身份、skill prompts、通道级稳定规则和时区解释规则留在稳定前缀；`plan.summary`、结构化会话记忆、`keyword_context`、`kb_context`、当前时间、文件根目录和风险等级进入最后一条 user 消息，减少每轮 system prefix 波动，提升 provider 自然前缀缓存命中机会。
- **会话记忆缓存（LRU）**：[`miniagent/memory/store.py`](../miniagent/memory/store.py) 的 `DefaultMemoryStore._cache` 使用 `OrderedDict` 实现 LRU 驱逐，默认上限 **200 会话**（`memory.store_cache_max`），命中时 `move_to_end` 提升活跃度，超限时 `popitem(last=False)` 驱逐最旧条目。
- **记忆存储异步 I/O**：[`miniagent/memory/store.py`](../miniagent/memory/store.py) 的 `load()` 和 `save()` 使用 `asyncio.to_thread()` 包装文件读写，避免阻塞事件循环。
- **飞书消息异步发送**：[`miniagent/feishu/im_send.py`](../miniagent/feishu/im_send.py) 新增 `post_im_message_async()`，使用 `asyncio.to_thread()` 包装同步 SDK 调用，避免阻塞事件循环。
- **紧凑 JSON 格式**：记忆文件使用紧凑 JSON（移除 `indent=2`），减少约 30% 文件体积和 20% 写入时间。
- **实例列表缓存延长**：缓存 TTL 从 5 秒提高到 **30 秒**，减少频繁目录遍历开销。
- **表格分隔符正则预编译**：[`miniagent/feishu/cards/gfm_table.py`](../miniagent/feishu/cards/gfm_table.py) 使用预编译 `_RE_GFM_SEPARATOR`。
- **嵌入索引上限**：[`miniagent/memory/embedding_search.py`](../miniagent/memory/embedding_search.py) 的 `EmbeddingIndex._entries` 有 `max_entries` 限制（默认 2000，`embedding.max_entries`），每条 ~12KB（1536 维 × 8 字节），2000 条约 24MB；超限驱逐最早条目。
- **活动日志读取缓存**：[`miniagent/memory/activity_log.py`](../miniagent/memory/activity_log.py) 的 `_read_today()` 有 30 秒内存缓存，避免每次 `log_session_start` 都读取 Growing 的 Markdown 文件。
- **历史消息浅拷贝**：[`miniagent/memory/history_bridge.py`](../miniagent/memory/history_bridge.py) 的 `conversation_history_for_llm()` 用 `v.copy()` 替代 `copy.deepcopy(v)`，对简单 `{role, content}` 消息快 5-10 倍。
- **预编译分词正则**：[`miniagent/memory/keyword_index.py`](../miniagent/memory/keyword_index.py) 的 `extract_keywords()` 使用模块级预编译 `_RE_NON_ALNUM_CJK` / `_RE_CJK_ONLY`，避免每次 `re.sub` 重新编译。
- **执行器 import 提升**：[`miniagent/core/executor.py`](../miniagent/core/executor.py) 的 `ToolResult` import 从 `_run_tool` 内部移到模块顶部，节省每次工具调用的 import 开销。
- **Trace writer 背压与统计真实性**：[`miniagent/infrastructure/tracing.py`](../miniagent/infrastructure/tracing.py) 的 `AsyncTraceWriter` 使用可配置有界队列，队列满时非阻塞丢弃并暴露 `dropped_count`；[`miniagent/infrastructure/trace_stats.py`](../miniagent/infrastructure/trace_stats.py) 聚合 `trace-YYYY-MM-DD.jsonl` 与 `trace-YYYY-MM-DD-pid*.jsonl`，日报不会漏读真实运行分片。

### 5.2 2026-06-07 业务热路径优化

本轮在保留功能、文件 schema 与用户可见效果不变的前提下，针对合成 perf 中最慢的记忆/索引路径和 Feishu thinking stream 重复渲染做代码级优化：

- [`DefaultMemoryStore`](../miniagent/memory/store.py)：`add_entry()` / `update_summary()` 在已持有 session lock 时走私有 `_load_unlocked()`，避免重复 public `load()` trace 与缓存路径；缓存命中、TTL 检查和 LRU 更新统一在 `_cache_lock` 下完成，减少竞态和重复时间戳读取。
- 记忆文件写盘改为紧凑 JSON（`separators=(",", ":")`）。字段含义、路径和可读性均保持兼容，只移除空白，降低批量 `add_entry()` 的写盘字节数。
- [`KeywordIndex`](../miniagent/memory/keyword_index.py)：索引文件改为紧凑 JSON；长中文文本关键词提取增加扫描预算上限，短文本仍保持全量 n-gram；多关键词搜索复用同一 `entry_key` 的 registry lookup；候选集较大时用 Top-K 选择避免全量排序。
- [`poll_server`](../miniagent/feishu/poll_server.py)：同一轮 Feishu thinking stream 缓存已规范化正文和 card JSON，正文或模板未变化时跳过重复 `_normalize_lark_md()` 与 `json.dumps()`；最终回复卡片对已分片、已规范化正文避免二次 normalize。

验证结果（Windows 本机，Python 3.12，2026-06-07）：

```text
python -m pytest tests/test_perf_synthetic.py -q -m perf --durations=13
13 passed in 9.15s
S6  memory batch + tracemalloc: 3.20s
S11 memory add_entry batch:      2.88s
S2  keyword batch save:          1.10s (本地运行有波动)
S13 Feishu thinking cache:       0.23s
S12 keyword multi-hit search:    0.04s
```

`python scripts/perf_profile_tracemalloc.py --inner-repeat 20 --json-out workspaces/logs/perf/perf-snapshot-business-hotpaths.json` 通过，tracemalloc peak 约 `41.74 MiB`。该输出位于 `workspaces/logs/perf/`，属于过程性文件，不提交。

残余风险：S2 在本机仍受临时目录 I/O 和 Python 冷启动影响有波动；Feishu 真实长驻路径仍建议用真实租户流量或 py-spy 采样确认 PATCH 节流、SDK 网络等待和 card JSON 序列化占比。

### 5.2.1 2026-06-11 Trace 完整性 + 阶段延迟优化

用真实 API（trace 持久化开启）+ `scripts/perf_trace_real_api.py` 跑通完整管线取得基线后，针对**串行 LLM 阶段的 trace 盲区**与**反思阶段延迟**做修复，全程保留功能与效果：

- **Trace 盲区补齐**：`classify` / `reflect` / `clarify` 阶段此前不发 trace 事件（实测各 ~5s 无任何记录）。现统一发出 `llm.request` / `llm.response`（含 `phase`、`session_key`、token usage）；planner 事件补 `session_key`；`compute_llm_stats` 新增 `by_phase` 分阶段聚合，可定位各阶段调用数与 token。
- **压缩事件归属修复**：[`DefaultContextManager`](../miniagent/memory/context.py) 新增 `session_key` 构造参数（executor 传入），`context.compress` 事件不再永远 `'unknown'`。
- **工具配对修复**：executor 的 `tool.start` / `tool.end` 补 `tool_call_id`，并发同名工具不再错配；移除不安全的 `semaphore._value` 私有属性访问；[`trace_stats`](../miniagent/infrastructure/trace_stats.py) 删除从不读取的 `tool_starts` 死字典。
- **死代码清理**：[`tracing.py`](../miniagent/infrastructure/tracing.py) 删除从未注册的 `_trace_file_hook` / `_init_trace_log_file`。
- **AsyncTraceWriter 健壮性**：`start()` 重复调用/线程创建失败时回收旧句柄与线程，避免 FD/线程泄漏；队列首次背压丢弃时打一次 warning（之后仅累计 `dropped_count`），避免静默丢数据；`metrics_only` 持久化保留**纯数值/布尔数组**（如时延分布），字符串数组仍按策略丢弃以不落模型文本。
- **反思阶段延迟**：反思是结构化 JSON 评分，无需深度思考与大输出。`llm_json()` 新增可选 `max_tokens` / `thinking_*` / `trace_*` 参数（默认行为不变）；`reflect_on_result` 施加 bounded 思考 + `features.reflection_max_tokens`（默认 512）。**实测单次 6.06s → 3.98s（-34%）**，可接受性判定与评分语义不变。

其他热路径（保留效果）：[`store.py`](../miniagent/memory/store.py) 缓存 TTL 清理从「每次 put 全表扫描持锁」改为按间隔惰性清理，`_session_locks` 加上限清理；[`keyword_index.py`](../miniagent/memory/keyword_index.py) 驱逐循环后只置一次 `_dirty`；[`embedding_search.py`](../miniagent/memory/embedding_search.py) 批量回退加 `_allow_batch` 守卫，消除维度不一致时的潜在无限递归；[`background_tasks.py`](../miniagent/engine/background_tasks.py) `create_task` 改为跟踪引用 + `add_done_callback`，不再静默丢异常。

验证：`ruff` 干净；trace 测试 16 passed；全量回归 1490 passed（唯一失败 `test_footer_survives_history_for_llm` 经 `git stash` 确认为干净树上的既存过期测试，与本轮无关）。

### 5.3 待验证 / 剖析指引

- **单次脚本 cProfile**：若不使用 `--inner-repeat`，top `cumtime` 多为导入链；见 §3.1。
- **Feishu `json.dumps`**：仍可能是线上热点；优化前应用 py-spy 对 `poll_server` 长驻路径采样确认；S5 仅覆盖 `_normalize_lark_md`，不替代整链 profiling。

### 5.4 异步最佳实践（新增）

为避免事件循环阻塞，在异步上下文中应使用异步版本函数：

| 同步函数（阻塞） | 异步函数（推荐在 async 中使用） | 文件 |
|-----------------|-------------------------------|------|
| `is_process_running()` | `is_process_running_async()` | `infrastructure/instance.py` |
| `InstanceRegistry.stop()` | `InstanceRegistry.stop_async()` | `infrastructure/instance.py` |
| `save_tasks()` | `save_tasks_async()` | `scheduled_tasks/store.py` |
| `is_in_git_repo()` | `is_in_git_repo_async()` | `core/self_opt/git_snapshot.py` |
| `has_uncommitted_changes()` | `has_uncommitted_changes_async()` | `core/self_opt/git_snapshot.py` |
| `create_snapshot()` | `create_snapshot_async()` | `core/self_opt/git_snapshot.py` |
| `rollback_snapshot()` | `rollback_snapshot_async()` | `core/self_opt/git_snapshot.py` |
| `post_im_message()` | `post_im_message_async()` | `feishu/im_send.py` |

**关键规则**：

1. **禁止在 async 函数中使用 `time.sleep()`**：
   - 使用 `await asyncio.sleep()` 替代
   - 阻塞 sleep 会暂停整个事件循环

2. **禁止在 async 函数中使用 `subprocess.run/check_output()`**：
   - 使用 `asyncio.create_subprocess_exec()` 替代
   - 同步 subprocess 会阻塞事件循环 5-60 秒

3. **禁止在 async 函数中使用 `urllib.request.urlopen()`**：
   - 使用 `httpx.AsyncClient` 或 `asyncio.to_thread()` 替代
   - 同步 HTTP 会阻塞事件循环 30 秒+

4. **跨进程锁无法改为 asyncio 锁**：
   - `tasks_json_lock()` 使用 `threading.RLock` + 文件锁
   - 解决方案：使用 `asyncio.to_thread()` 包装整个操作

**示例**：

```python
# ❌ 错误：阻塞事件循环
async def bad_example():
    output = subprocess.check_output(["tasklist"], timeout=5)  # 阻塞 5s
    time.sleep(0.1)  # 阻塞 0.1s
    save_tasks(tasks)  # 可能阻塞 0.35s

# ✅ 正确：不阻塞事件循环
async def good_example():
    running = await is_process_running_async(pid)
    await asyncio.sleep(0.1)
    await save_tasks_async(tasks)
```

## 6. 相关文件

| 文件 | 作用 |
|------|------|
| [`miniagent/core/request_payload.py`](../miniagent/core/request_payload.py) | S7：`serialize_exec_payload_sample`（执行轮次 messages/tools 序列化样本） |
| [`miniagent/engine/shutdown.py`](../miniagent/engine/shutdown.py) | 统一关停：定时任务、飞书、队列、子进程、实例注册 |
| [`tests/test_perf_synthetic.py`](../tests/test_perf_synthetic.py) | 合成 perf 用例（默认参与 `pytest -m "not evaluation"`） |
| [`scripts/perf_profile_tracemalloc.py`](../scripts/perf_profile_tracemalloc.py) | 本地可重复剖析入口（支持 `--inner-repeat`） |
| [`scripts/compare_perf_snapshots.py`](../scripts/compare_perf_snapshots.py) | 对比两次 `--json-out` JSON（峰值比例告警） |
