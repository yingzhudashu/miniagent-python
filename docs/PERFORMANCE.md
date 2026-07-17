# 性能测试与优化

> Mini Agent Python | 版本: 4.0.0 | 最后更新: 2026-07-17 | 与 `miniagent.__version__` 对齐 | 补充 [ENGINEERING.md](ENGINEERING.md)

本文分两部分：

- **Part A — 度量与测试**：进程内 KPI、合成场景矩阵、剖析命令、基线与 CI
- **Part B — 运行时调优**：配置优化、监控诊断、生产环境推荐配置

---

## Part A — 度量与测试

### 1. KPI 分层

| 层级 | 指标 | 说明 |
|------|------|------|
| **L1 本地** | `wall_local`（秒）、`alloc_delta`（字节，可选 tracemalloc）、**关键词索引写盘次数**、上下文 `json.dumps` 相关耗时 | 使用 **Mock LLM**，不访问外网；见 `tests/test_perf_synthetic.py` |
| **L2 剖析** | cProfile 累计时间、py-spy 火焰图、RSS | 开发机按需运行，见 §3 |
| **L3 端到端** | 单用例 wall time、p50/p95、token usage、错误率、trace 写入开销 | 依赖 API，必须显式设置 `MINIAGENT_REAL_API_STRESS=1`；见 `tests/evaluation/`、[docs/ENGINEERING.md](ENGINEERING.md) §3.2 |

**注意**：线上感知的 p95 延迟通常由 **LLM/HTTP** 主导；L1 回归用于防止 Python 侧退化，不替代端到端评测。

#### 1.1 性能变更验收门槛

对声明为性能优化的热路径，在同一机器、Python 版本、依赖、参数和预热策略下重复测量：主热点的中位指标必须至少改善 10%，相关 wall/CPU/RSS/分配指标不得回退超过 5%，并且功能、Trace 完整性和稳定性门禁全部通过。低于 10% 的变化按噪声处理，不宣称收益；超过 5% 的相关回退必须回滚或给出经评审的功能收益依据。真实 API p95 受网络和模型排队影响，只验证端到端行为与 Trace 契约，不单独作为 Python 热路径回归判据。

#### 1.2 进程关闭顺序（`shutdown_runtime`）

与 [miniagent/assistant/engine/shutdown.py](../miniagent/assistant/engine/shutdown.py) 实现一致，供排障与代码审阅对齐：

1. `LifecycleManager.stop()` 逆序停止 skills watcher、ticker、飞书和 config watcher，阻止产生新工作。
2. 取消并等待 `ApplicationContainer.shutdown_tracked_tasks`，随后关闭 `BackgroundTaskManager`。
3. 可选：`await message_queue.shutdown()`，取消并等待所有运行中、排队中和 `dispatch_wait` 任务的 `finally`。
4. `await memory.shutdown()`，停止 Dream 维护任务并关闭 embedding HTTP 池。
5. `cleanup_all_processes()`，持久化记忆索引，再关闭 OpenAI、飞书 Drive、HTML 上传、ClawHub 和共享 HTTP/browser 连接池。
6. 按需 `release_session_lock` 与 `unregister_instance()`；记录完整 shutdown span 后，最后 drain Trace writer。默认线程池始终交给解释器回收，避免 prompt_toolkit 退出时再次使用已关闭 executor。

**与 `run_cli_loop` 的关系**：`run_runtime` 在 `finally` 中唯一调用 `shutdown_runtime`。用户正常 `quit` 时循环已释放 session lock 并注销实例，因此传入两个 `False`；初始化、生命周期、CLI 异常与信号路径使用 `True`，覆盖未走循环清理的退出方式。

### 2. 场景矩阵（合成）

| ID | 场景 | 目的 |
|----|------|------|
| S1 | `execute_plan` + Mock 流式、1 次工具调用 | 执行器主路径本地耗时上界（宽松断言） |
| S2 | `DefaultMemoryStore.add_entry` 多次 + 单次 `flush_keyword_index` | 关键词索引 **合并落盘**（相对「每 add 一次 save」） |
| S3 | `DefaultContextManager` + 较多工具 schema 的 token 估算 | 上下文/序列化路径冒烟（阈值宽松） |
| S4 | 多工具下反复 `get_token_report`（burst） | 预算与报告路径；依赖工具 schema token **缓存**，防 `needs_compression` 相关路径退化 |
| S5 | `_normalize_lark_md` 大段正文 | 飞书 **纯 CPU** 规范化；不访问网络，与 `poll_server` 侧热点对照 |
| S6 | 批量 `add_entry` + `flush` 的 tracemalloc 峰值 | 旧兼容入口的分配量宽松上界；剖析脚本另覆盖正式 `record_turn` 路径 |
| S7 | `serialize_exec_payload_sample`（DefaultContextManager + messages/tools `json.dumps`） | 与 `execute_plan` 请求组装对齐的 Python 序列化冒烟 |
| S8 | 连续加载 260 个会话到 `DefaultMemoryStore` | 验证 LRU cache 驱逐（`memory.store_cache_max` 默认 200） |
| S9 | `EmbeddingIndex` 连续添加 250 条目 | 验证 `max_entries` 上限驱逐（200） |
| S10 | `KeywordIndex` 连续添加 200 条目 | 验证关键词数不超过 `max_entries`（测试中临时设为 50 以触发驱逐；生产默认见 `memory.keyword_index_max`） |

扩展场景时保持 **确定性**（固定 tmp 状态目录、Mock client），避免在默认 CI 中依赖网络。可选在 CI 中设置 **`PYTHONHASHSEED=0`**（见 `.github/workflows/perf-smoke.yml`）以降低 dict 迭代顺序带来的抖动。

### 3. 本地剖析命令

#### 3.1 cProfile（CPU）

对 `scripts/perf_profile_tracemalloc.py` **单次进程**跑 cProfile 时，累计时间常被 **importlib 冷启动** 主导；要突出内存批处理热路径，请加大 `--inner-repeat`（每次迭代使用独立子目录，避免状态无限膨胀）：

```bash
python -m cProfile -o perf.out scripts/perf_profile_tracemalloc.py --no-tracemalloc --inner-repeat 80
python -c "import pstats; p=pstats.Stats('perf.out'); p.strip_dirs().sort_stats('cumtime').print_stats(40)"
```

#### 3.2 tracemalloc（分配热点）

```bash
python scripts/perf_profile_tracemalloc.py --top 25
python scripts/perf_profile_tracemalloc.py --json-out perf-snapshot.json
python scripts/perf_profile_tracemalloc.py --inner-repeat 20 --json-out perf-snapshot.json
```

#### 3.3 py-spy（采样，需单独安装）

```bash
py-spy record -o profile.svg -- python scripts/perf_profile_tracemalloc.py --no-tracemalloc --inner-repeat 40
```

#### 3.4 两次剖析 JSON 对比（基线）

`perf_profile_tracemalloc.py --json-out` 写入的 JSON 含 `tracemalloc_peak_mib`、`inner_repeat` 等字段。将一次输出保存为 `tests/perf_baselines/<你的环境>.json` 后，可与新跑结果对比（**非门禁**，用于人工或可选告警）：

```bash
python scripts/compare_perf_snapshots.py tests/perf_baselines/my-baseline.json perf-snapshot.json --warn-ratio 1.35
```

对比 **tracemalloc_peak_mib** 时，请使两侧 JSON 的 **`inner_repeat` 与是否 `--no-tracemalloc`** 一致；否则脚本会打印 WARN，峰值可能不可比。`compare_perf_snapshots.py` 仅接受根为 **JSON 对象** 的文件（与 `perf_profile` 输出一致），勿将 `example.json` 的 `scenarios` 数组根文件误作输入。

#### 3.5 真实 API 压测（显式门禁）

真实 API 压测默认不会运行；即使存在 API key，也必须显式打开门禁，避免 CI 或本地全量测试意外产生费用：

```bash
set MINIAGENT_REAL_API_STRESS=1
set MINIAGENT_REAL_API_PERF_DIR=workspaces/logs/perf
python -m pytest tests/evaluation/test_perf_real_api.py -v -s
python scripts/perf_trace_real_api.py --scenario matrix --runs 3
```

压测使用当前 OpenAI-compatible 配置。`matrix` 运行纯回复、单 `read_file`、`list_dir + read_file` 各 3 次，再运行一轮 3 并发请求；工具矩阵只开放只读工具箱，不执行飞书、上传、命令或文件写操作。`perf_trace_real_api.py` 通过正式应用组合根构造依赖，在每次运行独立的 state、knowledge 和 trace 目录中执行，并在退出时关闭 OpenAI、memory、ClawHub、队列与 Trace writer；它只在内存中叠加隔离路径，不改写或复制 `config.user.json`。产物默认写入 `workspaces/logs/perf/`，属于过程性文件，不提交到仓库。

脚本强制 `metrics_only` 与 250ms CPU/RSS 资源采样，并自动验证 LLM `call_id` 配对、工具断言、terminal error、Trace drop/序列化/写入错误、秘密扫描和 writer 完整关闭。`summary.json` schema v3 包含 Git SHA、Python/平台、原始样本、中位数/p95、CPU、RSS、phase/span 和验证错误列表；只有显式启用 `trace.track_python_allocations` 时才包含 Python traced 指标。文件缺失、事件为空或 writer 终态计数不一致均会使脚本失败。产物不包含 prompt、response、工具参数或凭据。

#### 3.5.1 2026-07-16 真实验收摘要

环境：Windows 11、Python 3.12.7，使用当前 OpenAI-compatible 配置端点。过程产物位于 Git 忽略的 `workspaces/logs/perf-final/`。

| 场景 | 样本 | 中位数 | p95 | 结果 |
|---|---:|---:|---:|---|
| 纯回复 | 3 | 8.48s | 17.37s | 3/3 成功 |
| 单 `read_file` | 3 | 22.79s | 24.34s | 3/3 工具与回复成功 |
| `list_dir + read_file` | 3 | 75.21s | 95.81s | 3/3 两工具与回复成功 |
| 3 并发 | 3 | 7.72s | 10.79s | 3/3 成功 |

矩阵共写入 1702/1702 个事件，48/48 LLM 请求响应配对，`read_file` 6/6、`list_dir` 3/3；terminal error、drop、序列化错误、写入错误、关停不完整和秘密命中均为 0。工具平均耗时约 5ms，记忆持久化平均 27.2ms；主要耗时仍在 LLM/网络阶段，未通过删除规划、反思或工具效果换取数字。矩阵的 RSS 预热窗发生在首个应用对象图完成惰性加载前，warm-to-final 为 +33.64%，因此不作为泄漏判定；稳定性结论必须使用 §3.6 的有界重复负载浸泡。

同日另以 `MINIAGENT_REAL_API_STRESS=1` 执行 `tests/evaluation/test_perf_real_api.py`，provider-neutral 的纯模型、真实 `read_file`、三并发、模型配置和产物目录 5/5 通过。该入口不再硬编码 `OPENAI_API_KEY` 或已删除的执行器参数；有效凭据由所选 provider 的 gateway 构造统一验证。

新增高级配置均保持向后兼容：

| 配置 | 默认值 | 作用 |
|---|---:|---|
| `trace.resource_sample_interval_seconds` | `0` | 0 表示不启动资源采样线程；真实基准使用 0.25 |
| `trace.track_python_allocations` | `false` | 独立控制 tracemalloc；CPU/RSS 采样不再隐式开启 Python 分配跟踪 |
| `trace.writer_shutdown_timeout_seconds` | `5.0` | writer drain 超时；超时后保留 writer 引用并返回 `shutdown_incomplete`，允许调用方重试 |
| `trace.stats_max_latency_samples` | `100000` | 日报全局延迟 reservoir 上限，并按 phase 分配固定子预算 |
| `trace.stats_max_session_samples` | `1000` | 报告中保留的 session 列表上限；总数使用固定 bitmap 估计 |
| `embedding.index_queue_max_size` | `256` | 等待/执行中的异步向量索引任务容量；满载时调用方等待 |
| `embedding.index_concurrency` | `2` | embedding index 网络与写入并发上限 |

#### 3.6 Trace 开销与稳定性浸泡

```bash
python scripts/perf_trace_overhead.py --json-out workspaces/logs/trace-overhead.json
python scripts/perf_stability_soak.py --duration-seconds 1800 --interval-seconds 0.1 --json-out workspaces/logs/stability-soak.json
```

开销脚本分别测量无 hook 的禁用快路径与 `metrics_only` 持久化路径，并要求 writer 无 drop、序列化或写入错误；启用路径的宽松防灾难性门槛为 50µs/event。2026-07-16 最终同机样本为禁用 92.3ns/event、启用 5.31µs/event，14000/14000 写入成功。

浸泡脚本先执行 200 次有界工作单元，预热会话、上下文、关键词索引、tracemalloc、Trace writer/序列化/span，再启动资源采样；报告第 9–24 个稳态样本与末 16 个样本的 RSS/Python traced 中位平台变化，并检查线程回收和 writer 完整性。CI 每周运行 5 秒冒烟；发布验收使用 30 分钟，warm-to-final 平台增长门槛均为 5%。峰值减最小值包含惰性初始化，不能替代平台对比。

2026-07-16 最终 1800 秒验收完成 16703 次稳态迭代和 200 次预热，67838/67838 个事件写入成功；RSS warm-to-final 为 -0.25%，Python traced 为 +1.07%，进程 CPU 累计 40.45 秒，线程由预热后的 4 回落到 3、峰值 5。span failure、drop、序列化错误、写入错误、关停不完整和验证错误均为 0。

#### 3.7 可执行质量证据

性能结论只由调用真实实现的基准、Trace 开销、稳定性浸泡和资源曲线支撑。仓库不提交逐文件哈希或“已审查行数”生成台账，因为它们只能证明文件被扫描，不能证明实现正确，并会在普通代码变更后立即漂移。文档版本、命令覆盖、仓库卫生和链接由 `scripts/check_docs.py` 当次检查；代码正确性由 Ruff、Mypy、Bandit、架构门禁、分支覆盖率和行为测试共同验证。

### 4. 基线文件格式（`tests/perf_baselines/`）

用于人工或离线对比（**勿提交密钥**）。基线文件位于 `tests/perf_baselines/` 目录；首次使用请运行 `mkdir -p tests/perf_baselines` 创建。

字段建议：

- `schema_version`：整数，格式变更时递增
- `commit`：可选，Git SHA
- `generated_at`：ISO8601 UTC
- `environment`：`python`, `platform`
- `scenarios`：数组，元素含 `id`, `median_ms`, `notes`（与 L1 合成用例 id 对齐，便于 PR 描述引用）

另可将 `scripts/perf_profile_tracemalloc.py --json-out` 的产出 **复制** 为 `perf_baselines/tracemalloc_*.json`，供 `compare_perf_snapshots.py` 使用（该脚本读取的是脚本 JSON 格式，与 `example.json` 的 `scenarios` 数组可并存为不同文件）。

CI **不**依赖基线文件是否存在；可选 workflow 仅上传当次脚本输出 artifact。

### 5. 已确认/已缓解的热点（代码侧）

#### 5.1 已缓解

- **回合记忆与关键词索引**：标准 `DefaultMemoryStore` 通过 `record_turn()` 在同一会话锁内一次完成摘要、事实和条目更新，把正常回合的会话 JSON 写入从两次降为一次；注入的旧 MemoryStore 仍兼容 `update_summary()` + `add_entry()`。`KeywordIndex` 使用 dirty generation 和锁内一致快照，每轮 `flush_keyword_index_async()` 在线程中写盘，不阻塞事件循环；批量 `add_entry` 后仍可显式 `flush_keyword_index()`。正常关停时 `MemoryRuntime.close()` 统一持久化共享注册表、关键词索引与嵌入索引。关键词索引上限默认 20000（`memory.keyword_index_max`）。
- **上下文预算中的工具 schema**：[`miniagent/agent/context.py`](../miniagent/agent/context.py) 的 `DefaultContextManager` 对 `estimate_tool_tokens`（内部多次 `json.dumps(tool)`）做 **按次失效缓存**（调用 **`set_tools`** 或构造后首次用时计算；之后复用）。若需更新工具列表或 schema 内容，**必须**通过 `set_tools` 传入新列表，勿仅原地修改已绑定列表并依赖预算立即变化。
- **Prompt cache 友好分层**：执行阶段请求固定为 `stable system -> history -> current turn user context`。Agent 身份、skill prompts、通道级稳定规则和时区解释规则留在稳定前缀；`plan.summary`、结构化会话记忆、`keyword_context`、`kb_context`、当前时间、文件根目录和风险等级进入最后一条 user 消息，减少每轮 system prefix 波动，提升 provider 自然前缀缓存命中机会。
- **会话记忆缓存（LRU）**：[`miniagent/assistant/memory/store.py`](../miniagent/assistant/memory/store.py) 的 `DefaultMemoryStore._cache` 使用 `OrderedDict` 实现 LRU 驱逐，默认上限 **200 会话**（`memory.store_cache_max`），命中时 `move_to_end` 提升活跃度，超限时 `popitem(last=False)` 驱逐最旧条目。
- **记忆存储异步 I/O**：[`miniagent/assistant/memory/store.py`](../miniagent/assistant/memory/store.py) 的 `load()` 和 `save()` 使用 `asyncio.to_thread()` 包装文件读写，避免阻塞事件循环。
- **飞书消息异步发送**：[`miniagent/assistant/feishu/im_send.py`](../miniagent/assistant/feishu/im_send.py) 新增 `post_im_message_async()`，使用 `asyncio.to_thread()` 包装同步 SDK 调用，避免阻塞事件循环。
- **紧凑 JSON 格式**：记忆文件使用紧凑 JSON（移除 `indent=2`），减少约 30% 文件体积和 20% 写入时间。
- **实例列表缓存延长**：缓存 TTL 从 5 秒提高到 **30 秒**，减少频繁目录遍历开销。
- **表格分隔符正则预编译**：[`miniagent/assistant/feishu/cards/gfm_table.py`](../miniagent/assistant/feishu/cards/gfm_table.py) 使用预编译 `_RE_GFM_SEPARATOR`。
- **嵌入向量紧凑存储与分块查询**：[`miniagent/assistant/memory/embedding_search.py`](../miniagent/assistant/memory/embedding_search.py) 用连续 float64 数组替代 Python `list[float]`，API 缓存与索引可共享同一向量；500×1536 合成常驻分配由 23.65MiB 降至 6.25MiB（约 73.6%）。numpy 检索按 256 条构造临时矩阵，Top-K 与标量路径等价。索引仍受 `embedding.max_entries`（默认 2000）限制。
- **活动日志读取缓存**：[`miniagent/assistant/memory/activity_log.py`](../miniagent/assistant/memory/activity_log.py) 的 `_read_today()` 有 30 秒内存缓存，避免每次 `log_session_start` 都读取 Growing 的 Markdown 文件。
- **活动日志单一所有权**：`run_agent` 统一记录每轮首尾，executor 只在直接调用时兼容管理首尾，engine 不再二次写入或保留第二份完整工具结果。LLM、工具和兼容同步日志方法均通过异步适配器在线程执行。
- **历史消息浅拷贝**：[`miniagent/agent/history.py`](../miniagent/agent/history.py) 的 `conversation_history_for_llm()` 用 `v.copy()` 替代 `copy.deepcopy(v)`，对简单 `{role, content}` 消息快 5-10 倍。
- **历史预算线性裁剪**：`format_history_for_llm()` 只估算每条消息一次，再用单次后缀切片替代反复 `sum()+pop(0)`。同机 1000 条等价输出对比由 2.0751s 降至 0.0049s（约 427×）。`DefaultContextManager` 的 truncate 策略也改为累计待删 token 后一次切片，并发出 `strategy=truncate` 的 `context.compress` Trace。
- **预编译分词正则**：[`miniagent/assistant/memory/keyword_index.py`](../miniagent/assistant/memory/keyword_index.py) 的 `extract_keywords()` 使用模块级预编译 `_RE_NON_ALNUM_CJK` / `_RE_CJK_ONLY`，避免每次 `re.sub` 重新编译。
- **执行器 import 提升**：[`miniagent/agent/executor.py`](../miniagent/agent/executor.py) 的 `ToolResult` import 从 `_run_tool` 内部移到模块顶部，节省每次工具调用的 import 开销。
- **Trace writer 背压与统计真实性**：[`miniagent/agent/observability.py`](../miniagent/agent/observability.py) 的 `AsyncTraceWriter` 使用可配置有界队列，队列满时非阻塞丢弃并暴露 `dropped_count`；[`miniagent/assistant/infrastructure/trace_stats.py`](../miniagent/assistant/infrastructure/trace_stats.py) 聚合 `trace-YYYY-MM-DD.jsonl` 与 `trace-YYYY-MM-DD-pid*.jsonl`，日报不会漏读真实运行分片。
- **Trace 单遍聚合与安全清理**：日报和真实 API 阶段汇总不再构造整日事件列表；2 万条合成事件峰值由 18.81MiB 降至 0.06MiB。活动分片的 session 清理由 writer FIFO 独占执行，历史分片以临时文件流式原子替换；满队列不会牺牲维护命令，畸形 JSON 行会保留。`RuntimeAnalyzer` 复用同一聚合器，循环检测只保留有界前缀与计数。
- **队列与派生缓存上限**：完成的非 CLI chat 队列会立即回收，未知 chat 状态查询不再创建对象；工具箱 schema 组合缓存使用 128 项 LRU，防止模型生成不同 toolbox 组合造成长驻增长。
- **异步关停与飞书输出**：统一 shutdown 保持原资源顺序，但将索引/Trace/提案/锁等同步边界移入线程；飞书独立思考/反思卡使用异步发送，Docx 带统计追加只解析一次 Markdown。
- **启动导入图**：`miniagent.assistant.engine`、`miniagent.assistant.memory` 与 `miniagent.agent.types` 聚合包使用惰性导出；OpenAI schema 类型仅在静态类型检查时导入。2026-07-12 同机基线中，`engine.main` 冷导入从约 4.75s / 45.05MiB 降至 0.79s / 17.74MiB，合成 tracemalloc 峰值从 44.21MiB 降至 21.18MiB。导出兼容性和全新进程循环导入由 `tests/test_package_lazy_imports.py` 覆盖。
- **明确的无工具执行**：`StructuredPlan.tools_enabled=False` 区分“禁止工具”与 `required_toolboxes=[]` 的“不过滤”语义。仅在调用方没有工具箱或用户明确要求不调用工具时关闭工具；其他简单任务仍保留原工具能力。真实同提示对比中执行输入 token 约下降 33%，端到端耗时约下降 26%。
- **知识库热路径惰性导入**：仅调用 `retrieve_knowledge_context()` 时不再加载 `KnowledgeRegistry`、PyYAML、文件摄取与索引栈；`KnowledgeRegistry` 的公共导出保持兼容。S1 稳态计时先执行一次明确 warm-up，避免把模块/正则一次性初始化和测试夹具 GC 误判为执行抖动，原阈值不放宽。
- **索引原子持久化与 embedding single-flight**：共享注册表、关键词索引、embedding 索引和会话历史先写同目录唯一临时文件，再用 `os.replace()` 发布；save 使用串行锁与 generation，旧快照不会清除并发变更的 dirty。相同 embedding 文本的并发 miss 共享一个 API task，取消单个等待者不会取消公共请求。
- **后台任务并发槽位**：后台任务在创建协程时即预留槽位，避免多个并发 `start_task()` 在协程尚未调度时共同绕过上限；正常、失败、启动前取消和 shutdown 都幂等释放槽位。
- **飞书缓存与连接复用**：消息去重按单键严格执行 5 分钟 TTL 并原子 flush；tenant token 的同步/异步并发 miss 各自合并为一次请求；HTML 上传三类操作按事件循环复用 aiohttp 连接池并在统一 shutdown 中关闭。
- **Trace 重试与终态语义**：`failed_response_count` 与 `attempt_error_rate` 保留失败尝试诊断；`retrying_response_count`、`terminal_failed_response_count` 和 `error_rate` 描述操作终态。真实运行中 2 次网关重试恢复后，尝试失败率为 33.3%，终态失败率正确为 0，且请求/响应无失配。
- **Trace 资源与安全白名单**：生产默认 `trace.resource_sample_interval_seconds=0`；真实基准显式设为 0.25s 并惰性加载 psutil。CPU/RSS 采样与 tracemalloc 已拆分，后者只在 `trace.track_python_allocations=true` 时启用。持久化只接受事件类型/模型/phase 等安全字符串和已登记的数字/布尔指标；未知字符串、未知数字标识、嵌套对象、错误正文、prompt/response/args 一律丢弃。hook 仍收到原事件。
- **Trace 配置、schema 与生命周期**：组合根显式构造不可变 `TraceRuntimeConfig`，避免 observability 反向读取应用配置；初始化失败会完整回滚并允许重试。所有持久化事件自动带 `trace_schema_version`，`TRACE_EVENT_TYPES` 是生产 emitter 的静态契约。writer 关停超时会报告 `shutdown_incomplete` 并保留可重试状态，维护命令与普通事件通过同一 enqueue 锁保持顺序。
- **Trace 跨日与大分片**：writer 按事件 UTC 日期安全切换分片，自定义无日期路径也能跨日；关闭超时时不关闭仍被线程使用的句柄。日报对延迟、session、phase/tool/error/span 基数均设固定容量，坏 JSON、NaN 和畸形字段不会中止聚合。
- **LLM 参数能力学习**：仅对明确 HTTP 400 “参数不受支持”学习 `temperature/top_p`，缓存键包含弱引用 client、endpoint、model 与 wire API，并有 LRU 上限；非法参数值和其他 400 不会污染能力缓存。后续请求直接采用已学习的兼容参数，保留现有重试、协议降级与思考等级。
- **Embedding 异步索引**：空索引在 query embedding 前直接返回；耐久记忆和关键词索引完成后，向量索引进入默认容量 256、并发 2 的队列。满队列反压而不丢条目，下一次搜索和 shutdown 均完整 drain；Trace 区分 query/index、缓存、queue wait、network 和 index duration，并继承 session/span。
- **工具大输入与原子发布**：`read_file` 单遍完成分页、准确行数、完整 hash 与有界 RAG 内容；目录列表辅助内存受 `max_entries` 约束；JSONL 逐行解析，CSV/JSON/命令输出有类型与容量上限。文件、CSV、JSON、会话记忆和自动知识镜像均先写同目录临时文件，再原子替换，失败保留旧文件。
- **长驻监控与 CLI 渲染**：工具错误样本、可选延迟样本、Markdown Console/render cache 均固定容量；render cache 同时受条目和 8MiB 限制，共享 Console 的输出句柄切换受锁保护，终端宽度变化不会线性累积实例。

#### 5.2 待验证 / 剖析指引

- **单次脚本 cProfile**：若不使用 `--inner-repeat`，top `cumtime` 多为导入链；见 §3.1。
- **Feishu `json.dumps`**：仍可能是线上热点；优化前应用 py-spy 对 `poll_server` 长驻路径采样确认；S5 仅覆盖 `_normalize_lark_md`，不替代整链 profiling。

#### 5.3 异步最佳实践

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

### 6. 相关文件

| 文件 | 作用 |
|------|------|
| [`miniagent/agent/request_payload.py`](../miniagent/agent/request_payload.py) | S7：`serialize_exec_payload_sample`（执行轮次 messages/tools 序列化样本） |
| [`miniagent/assistant/engine/shutdown.py`](../miniagent/assistant/engine/shutdown.py) | 统一关停：定时任务、飞书、队列、子进程、实例注册 |
| [`tests/test_perf_synthetic.py`](../tests/test_perf_synthetic.py) | 合成 perf 用例（默认参与 `pytest -m "not evaluation"`） |
| [`scripts/perf_profile_tracemalloc.py`](../scripts/perf_profile_tracemalloc.py) | 本地可重复剖析入口（支持 `--inner-repeat`） |
| [`scripts/compare_perf_snapshots.py`](../scripts/compare_perf_snapshots.py) | 对比两次 `--json-out` JSON（峰值比例告警） |
| [`scripts/perf_trace_overhead.py`](../scripts/perf_trace_overhead.py) | Trace 禁用/启用路径开销与完整写入校验 |
| [`scripts/perf_stability_soak.py`](../scripts/perf_stability_soak.py) | 有界混合负载的 RSS、Python 分配和线程稳定性浸泡 |

---

## Part B — 运行时调优

本部分提供配置级优化策略，帮助提升 Agent 响应速度和资源利用率。度量与回归测试见 Part A。

### B.1 内存优化

#### 历史压缩策略

**问题**：会话历史过长，占用内存过多。

**优化方案**：

1. **限制历史长度**（默认 `memory.history_tail_messages` 为 **200**）：
   ```json
   {
     "memory": {
       "history_tail_messages": 100
     }
   }
   ```

2. **限制 transcript 体积**（默认 `memory.max_transcript_chars` 为 **400000**）：
   ```json
   {
     "memory": {
       "history_tail_messages": 100,
       "max_transcript_chars": 500000
     }
   }
   ```
   注：自动归档逻辑见 `memory/history_archive.py`。

3. **定期清理**：删除不再使用的会话目录（`/session delete <id>` 或手动移除 `{paths.state_dir}/sessions/<id>/`）。

#### 记忆分层清理

**问题**：三层记忆文件膨胀。

**优化方案**：

1. **短期记忆**：缓存最多 `memory.store_cache_max` 个会话（默认 200），超过限制自动清理。
2. **活动日志**：按日写入 `{paths.state_dir}/memory/YYYY-MM-DD.md`；无独立 `activity_log_retention_days` 配置项，需手动归档旧文件。
3. **关键词索引**：
   ```json
   { "memory": { "keyword_index_max": 15000 } }
   ```

#### 缓存大小调整

1. **工具注册表缓存**：
   ```json
   { "memory": { "registry_max_entries": 2000 } }
   ```
2. **飞书去重缓存**：每个 `FeishuPollState` 独立持有；处理中 claim 与已完成 ID 均严格执行 TTL，加载时剔除过期记录，运行时关闭时串行、原子地异步 flush。
3. **嵌入搜索缓存**（关闭可省内存）：
   ```json
   { "embedding": { "enabled": false } }
   ```

### B.2 执行优化

#### 并行工具调用

```json
{ "agent": { "allow_parallel_tools": true } }
```

**效果**：总耗时 ≈ 最慢单个工具耗时（提升 50–70%）。适用于多个独立文件读取、搜索或无依赖关系的工具调用。

#### 流式输出

CLI 与飞书默认以流式展示思考过程（引擎层行为，无 `agent.streaming` 配置项）。渲染细节见 [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)。

#### Token 估算优化

1. 使用 tiktoken 库精确计数（见 `executor.py`）。
2. 调整预算：
   ```json
   {
     "model": { "context_window": 128000, "max_tokens": 4096 }
   }
   ```
3. 上下文压缩由 `agent.context_compress_threshold`（默认 0.6）与 `memory/context.py` 自动触发，可调：
   ```json
   {
     "agent": { "context_compress_threshold": 0.5 },
     "memory": { "history_tail_messages": 100 }
   }
   ```

### B.3 网络优化

#### API 调用优化

```json
{
  "agent": { "http_timeout": 120 },
  "model": { "retry_count": 2, "model": "gpt-4o-mini" }
}
```

用户 JSON 键为 **`model.retry_count`**（非 `max_retries`）。`infrastructure/http_retry.py` 与 OpenAI SDK 内部参数名也可能叫 `max_retries`，属于实现细节，勿写成 `config.user.json` 键。OpenAI SDK 超时在 `core/openai_client.py` 中配置为 120 秒。

#### 飞书连接优化

```json
{
  "feishu": {
    "websocket": {
      "auto_reconnect": false,
      "watchdog_interval": 30,
      "dead_conn_grace": 90,
      "refresh_interval": 3600
    }
  }
}
```

内置 Web 技能的 Tavily、静态页面抓取与下载工具由运行时按事件循环复用同一组有界
HTTPX 连接池；浏览器和 Playwright driver 由基础设施层成对持有，并在空闲回收、创建失败和
统一 shutdown 时关闭。技能热重载不会接管这些长生命周期资源。Lark SDK 客户端按
`app_id + 密钥指纹` 复用，同一 app 密钥轮换会原子替换旧实例，缓存最多保留 8 项。

会话列表使用 `config.json` 的 `mtime_ns + size` 指纹复用已解析的紧凑元数据，缓存最多
2048 项；外部原子替换会自动失效。该缓存为内部实现，无需新增用户配置。

### B.4 监控与诊断

**查看工具调用统计**：`/stats`

**本地剖析与回归测试**：见 Part A §2（场景矩阵）、§3（剖析命令）。合成 perf 回归：

```bash
pytest -m perf tests/test_perf_synthetic.py -xvs
```

真实 API Trace harness 默认拒绝运行，需显式设置 `MINIAGENT_REAL_API_STRESS=1`；它使用隔离状态目录和
`metrics_only` Trace，不写入 prompt、response、工具参数或凭据：

```bash
MINIAGENT_REAL_API_STRESS=1 python scripts/perf_trace_real_api.py --runs 1
```

**关键指标参考**：

- 内存占用 < 500 MB
- 平均响应时间 < 30 秒
- 工具成功率 > 95%
- LLM Token 使用率 < 80%

### B.5 最佳实践

**每周维护**：

```bash
/session list
/stats
# 按需：/session delete <旧会话ID>
```

**生产环境推荐配置**：

```json
{
  "memory": {
    "history_tail_messages": 100,
    "store_cache_max": 200,
    "initial_history_count": 5,
    "max_transcript_chars": 400000
  },
  "agent": {
    "parallel_sessions": true,
    "max_parallel_sessions": 4,
    "allow_parallel_tools": true,
    "tool_timeout": 60,
    "http_timeout": 120
  },
  "model": {
    "model": "gpt-4o-mini",
    "retry_count": 2,
    "max_tokens": 4096
  }
}
```

### B.6 性能问题排查清单

1. 内存占用过高？ → 清理历史和记忆（见 B.1）
2. 响应缓慢？ → 启用并行工具、检查模型与网络（见 B.2–B.3）
3. API 超时？ → 增加超时时间和重试次数（见 B.3）
4. 飞书无响应？ → 检查连接状态和凭证（见 [FEISHU.md](FEISHU.md)、[TROUBLESHOOTING.md](TROUBLESHOOTING.md)）
5. Token 超限？ → 调整上下文窗口和压缩策略（见 B.2）
