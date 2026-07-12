# 性能与 Trace 逐文件审计台账

> 状态：进行中 | 最近更新：2026-07-12

本文是性能增强工作的可验证台账，不替代架构或用户文档。每一项审查均记录：运行路径、时间/空间复杂度、同步 I/O、资源所有权、并发边界、Trace 可观测性、敏感数据边界、兼容性和回归证据。只有经过代码审查、自动测试和适用的真实 API 验证后，条目才标记为“已验证”。

## 验收基线

| 指标 | 优化前 | 当前证据 | 状态 |
|---|---:|---:|---|
| 合成性能、Trace 与惰性导入专项 | 18 passed / 约 0.81s | 36 passed / 3.39s（覆盖项显著增加） | 通过 |
| 合成 tracemalloc 峰值 | 44.21MiB | 约 16.29MiB | 约下降 63% |
| 2 万条 Trace 日报聚合峰值 | 18.81MiB（整日列表） | 0.06MiB（单遍流式） | 约下降 99.7% |
| 1000 条历史预算裁剪 | 2.0751s（重复求和/头删） | 0.0049s（单次计数/切片） | 约加速 427 倍 |
| 500×1536 embedding 常驻分配 | 23.65MiB（Python float 列表） | 6.25MiB（连续 float64） | 约下降 73.6% |
| `engine.main` 冷导入 | 4.75s / 45.05MiB | 0.79s / 17.74MiB | 时间约下降 83%，峰值约下降 61% |
| 明确无工具的真实请求 | 17.32s；exec 输入 9,884 token | 12.77s；exec 输入 6,610 token | 耗时约下降 26%，exec token 约下降 33% |
| 真实规划工具闭环 | 无可靠 harness | 最近一次 15.71s；`read_file` 15ms；16/16 Trace 写入；4/4 LLM 配对 | 通过 |
| 非 evaluation 全量测试 | 既有基线 | 2406 passed，3 skipped，6 deselected | 通过 |

真实性能数字受网络与上游调度影响，只用于同机、同提示、同配置的方向性对比；功能正确性由测试、Trace 配对和工具结果共同验证。

## 已审查文件

| 文件 | 审查结论与改动 | 证据 |
|---|---|---|
| `infrastructure/tracing.py` | 修复 10ms 空轮询、低流量逐事件 flush、满队列关闭丢事件、大批次关闭等待、序列化静默丢失；活动分片会话清理由 writer FIFO 独占重写，后续与排队事件同步过滤；紧凑 JSON；最终 writer 统计可返回 | 活动分片/满队列维护命令/继续写专项；真实运行 16/16 写入，零丢弃/错误 |
| `infrastructure/trace_stats.py` | 同时读取基础/PID 分片；日报单遍流式聚合；历史分片常量辅助内存重写；统一 Chat/Responses usage；修复 `context.compress` 字段别名；缓存/推理 token、错误率、message/tool 数、平均/p50/p95 时延和分阶段统计 | 2 万事件峰值 18.81→0.06MiB；畸形行保留；真实请求/响应零失配 |
| `core/self_opt/runtime_analyzer.py` | 复用统一流式聚合；循环检测只保留每会话工具计数和前 6 次调用，不再持有整日事件或完整调用序列 | self-opt 集成/循环模式专项 |
| `core/task_classifier.py` | 每次尝试记录安全 duration、message/tool 数；保留既有有界恢复 | 分类专项与真实 API（可恢复失败可见） |
| `core/llm_json.py` | JSON 控制阶段记录逐尝试 duration 和请求规模 | JSON/反思专项与真实反思请求 |
| `core/planner.py` | 规划尝试记录 duration 和请求规模，失败与恢复可分辨 | planner 专项；真实规划一次恢复后成功 |
| `core/executor.py` | 执行尝试记录 duration、attempt、model、usage；支持计划明确关闭工具；OpenAI 类型改为静态导入 | executor 专项；真实无工具与双文件工具闭环 |
| `core/agent.py` | 区分用户明确禁用工具与空工具箱；一般简单任务工具能力不变；顶层 `session_key` 回填唯一的分组配置来源，分类/规划/执行/反思 Trace 归属一致 | 无工具真实对比；阶段配置单测；真实报告由 2 个会话恢复为 1 个 |
| `types/planning.py` | `tools_enabled` 建立无工具语义，避免复用 `required_toolboxes=[]` 的“不筛选”含义 | 工具选择单元测试 |
| `engine/__init__.py` | eager 聚合改为缓存式惰性导出，保留可选 `ThinkingDisplay=None` 兼容 | 全新进程导入测试；冷启动基线 |
| `memory/__init__.py` | eager 聚合改为缓存式惰性导出，消除 core↔memory↔engine 循环导入 | `import miniagent.core.agent` 全新进程成功 |
| `memory/store.py`、`memory/memory_context_service.py` | 正常回合将摘要、事实、完整条目合并为一次锁内 load/modify/write；旧 MemoryStore 仍走兼容两步；整轮工具结果参与事实提取；关键词 flush 移到工作线程 | 单轮写入次数断言、旧协议兼容、工具事实和事件循环 heartbeat 测试 |
| `memory/shared_registry.py`、`memory/keyword_index.py` | 注册表与关键词倒排索引增加 RLock、变更 generation 和一致快照；搜索线程与事件循环写入不再并发遍历同一容器；并发变更不会被旧快照错误清除 dirty | registry/index/memory runtime 专项 63 passed |
| `memory/history_bridge.py`、`memory/context.py` | 历史预算裁剪由 O(n²) 改为 O(n)；truncate 由逐条头删改为单次切片，并补齐压缩 Trace；消息清洗/输出顺序不变 | 1000 条等价输出基准约 427×；历史、overflow、Trace 指标测试 |
| `memory/embedding_search.py` | API 缓存与索引共享连续 float64 向量，保留数值精度；numpy 搜索按 256 条分块，避免每次查询复制完整矩阵；`limit=0` 行为修正 | 500×1536 常驻分配下降约 73.6%；批量/标量 Top-K 等价测试 |
| `memory/activity_log.py`、`engine/engine.py` | `run_agent` 单点拥有活动日志首尾；engine 不再重复保存摘要/完整工具结果；同步兼容实现自动在线程执行 | 单次首尾/engine 不重复写/150ms heartbeat 专项 |
| `infrastructure/message_queue.py` | 非 CLI chat 最后一个任务完成后回收；未知状态查询不再创建队列；shutdown 清空持有图 | 250 个瞬时 chat 后队列表为空；并行/abort/shutdown 回归 |
| `infrastructure/registry.py` | 任意 LLM toolbox 组合的派生 schema 缓存改为 128 项 LRU，注册/注销仍整体失效 | 300 组合驱逐与筛选结果回归 |
| `feishu/docx/blocks.py`、`feishu/poll_server.py` | 带 stats 文档追加复用一次 Markdown AST 结果；独立思考与反思卡改走异步发送 | parse 调用次数断言；Docx fallback、反思卡、merge-tools 回归 |
| `engine/shutdown.py` | 会话状态、记忆索引、Trace join/清理、提案、锁和实例注册等同步边界移出事件循环，资源关闭顺序不变 | shutdown 顺序/幂等/heartbeat 专项 63 项 |
| `types/__init__.py` | eager 类型聚合改为缓存式惰性导出 | 导出兼容测试；冷启动基线 |
| `types/tool.py`、`memory/context.py`、`infrastructure/registry.py` | OpenAI schema 仅用于静态检查，运行时使用等价 dict 注解，避免加载 SDK 全部类型树 | `types.config` 导入不含 `openai`；全量测试 |
| `infrastructure/json_config.py` | 新增只在内存中生效的隔离 overlay，不写用户配置 | 配置文件不变测试 |
| `scripts/perf_trace_real_api.py` | 迁移到当前组合根；隔离状态；完整资源关闭；安全阶段报告和请求/响应配对 | mock 脚本测试；两次真实 API 验证 |

## 待审查队列

按风险与实测收益排序，逐批推进：

1. `embedding_search.py` 的跨线程缓存边界与索引原子持久化；当前向量/查询峰值已完成。
2. `tools/*` 剩余 schema 体积、外部客户端复用和工具并发路径。
3. `feishu/*` 剩余长连接、去重持久化、Drive token 并发和卡片出站边界。
4. `engine/*` 与 `session/*` 剩余启动、历史扫描、后台任务和异常关停路径。
5. 其余 `miniagent/`、`scripts/` 与关键测试文件；逐项确认无无界缓存、阻塞 async I/O、重复解析或资源泄漏。

## 每批必过门禁

```text
Ruff
mypy application/bootstrap/contracts/types
mypy --follow-imports=silent 模型控制链
architecture check
compileall
git diff --check
pytest tests -q -m "not evaluation"
适用时：合成 perf、tracemalloc、真实 API Trace
```

真实 Trace 始终使用 `metrics_only`，不得记录 API key、认证头、完整 prompt、完整 response 或工具参数正文。
