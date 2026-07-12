# 性能与 Trace 逐文件审计台账

> 状态：进行中 | 最近更新：2026-07-12

本文是性能增强工作的可验证台账，不替代架构或用户文档。每一项审查均记录：运行路径、时间/空间复杂度、同步 I/O、资源所有权、并发边界、Trace 可观测性、敏感数据边界、兼容性和回归证据。只有经过代码审查、自动测试和适用的真实 API 验证后，条目才标记为“已验证”。

## 验收基线

| 指标 | 优化前 | 当前证据 | 状态 |
|---|---:|---:|---|
| 合成性能、Trace、惰性导入与资源生命周期专项 | 18 passed / 约 0.81s | 95 passed / 15.74s（覆盖项显著增加） | 通过 |
| 合成 tracemalloc 峰值 | 44.21MiB | 约 16.29MiB | 约下降 63% |
| 2 万条 Trace 日报聚合峰值 | 18.81MiB（整日列表） | 0.06MiB（单遍流式） | 约下降 99.7% |
| 1000 条历史预算裁剪 | 2.0751s（重复求和/头删） | 0.0049s（单次计数/切片） | 约加速 427 倍 |
| 500×1536 embedding 常驻分配 | 23.65MiB（Python float 列表） | 6.25MiB（连续 float64） | 约下降 73.6% |
| `engine.main` 冷导入 | 4.75s / 45.05MiB | 0.79s / 17.74MiB | 时间约下降 83%，峰值约下降 61% |
| 明确无工具的真实请求 | 17.32s；exec 输入 9,884 token | 12.77s；exec 输入 6,610 token | 耗时约下降 26%，exec token 约下降 33% |
| 真实规划/执行工具闭环 | 无可靠 harness | 最新连续两轮 24.76s / 24.36s；`read_file` 平均 16ms；36/36 Trace 写入；10/10 LLM 配对；无终态或尝试失败 | 通过 |
| 1000 会话重复列表 / 编号解析 | 134.4ms / 103.6ms | 13.3ms / 8.1ms | 约加速 10× / 13× |
| 3000 会话扫描峰值 | 5.60MiB | 2.32MiB（缓存硬上限 2048） | 约下降 59% |
| 500 个短会话后的 Session 锁表 | 500 项（仅 10 项驻留） | 10 项（与驻留 LRU 一致） | 无界增长已消除 |
| 50 次 HTTPX 客户端获取 | 逐调用创建/关闭约 20.6s | 复用 1 个池约 0.41s | 约下降 98% |
| 非 evaluation 全量测试 | 既有基线 | 2449 passed，3 skipped，6 deselected | 通过 |

真实性能数字受网络与上游调度影响，只用于同机、同提示、同配置的方向性对比；功能正确性由测试、Trace 配对和工具结果共同验证。

## 已审查文件

| 文件 | 审查结论与改动 | 证据 |
|---|---|---|
| `infrastructure/tracing.py` | 修复 10ms 空轮询、低流量逐事件 flush、满队列关闭丢事件、大批次关闭等待、序列化静默丢失；活动分片会话清理由 writer FIFO 独占重写，后续与排队事件同步过滤；紧凑 JSON；最终 writer 统计可返回 | 活动分片/满队列维护命令/继续写专项；真实运行 16/16 写入，零丢弃/错误 |
| `infrastructure/trace_stats.py` | 同时读取基础/PID 分片；日报单遍流式聚合；历史分片常量辅助内存重写；统一 Chat/Responses usage；修复 `context.compress` 字段别名；缓存/推理 token、message/tool 数、平均/p50/p95 时延和分阶段统计；区分失败尝试率与终态失败率，恢复成功不再污染稳定性指标 | 2 万事件峰值 18.81→0.06MiB；畸形行保留；真实请求/响应零失配；真实执行 2 次重试但终态失败率为 0 |
| `infrastructure/tracing.py`、`infrastructure/trace_stats.py`、四类 LLM 控制/执行调用点 | `llm.request` 增加纯数字 `message_chars` / `tool_schema_chars`，线性计长且不序列化或保存正文；日报提供分阶段平均值，可将本地请求膨胀与上游 token 计量波动分离 | 同进程连续两轮分类/规划字符数严格一致（3309 / 11031），执行 schema 均为 1809；36 行 Trace 禁止字段与配置凭据值命中均为 0 |
| `core/self_opt/runtime_analyzer.py` | 复用统一流式聚合；循环检测只保留每会话工具计数和前 6 次调用，不再持有整日事件或完整调用序列 | self-opt 集成/循环模式专项 |
| `core/task_classifier.py` | 每次尝试记录安全 duration、message/tool 数及是否继续重试；协议降级请求/响应保持配对；保留既有有界恢复 | 分类专项与真实 API（可恢复失败可见且不计为终态失败） |
| `core/llm_json.py` | JSON 控制阶段记录逐尝试 duration、请求规模与重试终态；`json_object` 降级也补齐响应事件 | JSON/反思专项与真实反思请求 |
| `core/planner.py` | 规划尝试记录 duration、请求规模和重试终态；协议降级的两次 HTTP 调用分别配对 | planner 专项；真实规划恢复后成功 |
| `core/executor.py` | 执行尝试记录 duration、attempt、model、usage 与重试终态；最终异常/空响应也补齐 response Trace；支持计划明确关闭工具；OpenAI 类型改为静态导入 | executor 专项；真实无工具与工具闭环；真实 6/6 LLM 配对 |
| `core/agent.py` | 区分用户明确禁用工具与空工具箱；一般简单任务工具能力不变；顶层 `session_key` 回填唯一的分组配置来源，分类/规划/执行/反思 Trace 归属一致 | 无工具真实对比；阶段配置单测；真实报告由 2 个会话恢复为 1 个 |
| `types/planning.py` | `tools_enabled` 建立无工具语义，避免复用 `required_toolboxes=[]` 的“不筛选”含义 | 工具选择单元测试 |
| `engine/__init__.py` | eager 聚合改为缓存式惰性导出，保留可选 `ThinkingDisplay=None` 兼容 | 全新进程导入测试；冷启动基线 |
| `knowledge/__init__.py` | `retrieve_knowledge_context` 热路径不再提前导入 Registry、PyYAML、文件摄取和索引栈；`KnowledgeRegistry` 保持惰性兼容导出 | 全新进程导入边界；S1 组合性能连续三轮稳定通过 |
| `memory/__init__.py` | eager 聚合改为缓存式惰性导出，消除 core↔memory↔engine 循环导入 | `import miniagent.core.agent` 全新进程成功 |
| `memory/store.py`、`memory/memory_context_service.py` | 正常回合将摘要、事实、完整条目合并为一次锁内 load/modify/write；旧 MemoryStore 仍走兼容两步；整轮工具结果参与事实提取；关键词 flush 移到工作线程 | 单轮写入次数断言、旧协议兼容、工具事实和事件循环 heartbeat 测试 |
| `infrastructure/atomic_json.py`、`memory/shared_registry.py`、`memory/keyword_index.py` | 注册表与关键词倒排索引使用 RLock、变更 generation、一致快照、串行 save 和同目录原子替换；搜索线程与事件循环写入不再并发遍历同一容器；失败写入保留旧文件，并发变更不会被旧快照错误清除 dirty | 原子写入失败保留专项；registry/index/memory runtime 回归 |
| `memory/history_bridge.py`、`memory/context.py` | 历史预算裁剪由 O(n²) 改为 O(n)；truncate 由逐条头删改为单次切片，并补齐压缩 Trace；消息清洗/输出顺序不变 | 1000 条等价输出基准约 427×；历史、overflow、Trace 指标测试 |
| `memory/embedding_search.py` | API 缓存与索引共享连续 float64 向量；numpy 搜索按 256 条分块；缓存、NumPy 惰性加载和索引增加跨线程保护；索引使用 generation 快照、串行原子 save；相同文本并发 miss 复用单个 API task，单个等待者取消不影响其他调用方 | 常驻分配下降约 73.6%；批量/标量 Top-K 等价；并发 save、single-flight 与取消专项 |
| `memory/activity_log.py`、`engine/engine.py` | `run_agent` 单点拥有活动日志首尾；engine 不再重复保存摘要/完整工具结果；同步兼容实现自动在线程执行 | 单次首尾/engine 不重复写/150ms heartbeat 专项 |
| `infrastructure/message_queue.py` | 非 CLI chat 最后一个任务完成后回收；未知状态查询不再创建队列；shutdown 清空持有图 | 250 个瞬时 chat 后队列表为空；并行/abort/shutdown 回归 |
| `infrastructure/registry.py` | 任意 LLM toolbox 组合的派生 schema 缓存改为 128 项 LRU，注册/注销仍整体失效 | 300 组合驱逐与筛选结果回归 |
| `engine/background_tasks.py`、`engine/bg_session_cleanup.py`、`session/manager.py` | 后台任务在创建时原子预留并发槽位，启动前取消与正常 finally 幂等释放；同步清理移入线程，但 SessionManager 容器仍由事件循环线程移除；历史/配置改为原子写入，销毁临时会话不再先写后删 | 并发 start 上限、取消计数、清理 heartbeat、历史并发保存与临时会话专项 |
| `session/manager.py`、`engine/engine.py` | `scandir` + 文件指纹复用紧凑会话元数据，缓存硬上限 2048 且检测外部原子替换；per-session RLock 引用计数后随 LRU/销毁回收；回合末历史保存转入工作线程并等待完成，不再阻塞事件循环 | 100/1000/3000 会话同机基准；外部替换、缓存上限、500 会话锁回收、异步持久化 heartbeat 与历史一致性专项 |
| `feishu/feishu_dedup.py`、`feishu/drive_client.py`、`engine/feishu_state.py` | 去重记录按单键严格执行 TTL、加载时裁剪、串行原子 flush；tenant token 的同步/异步并发 miss 分别合并；飞书后台任务终态异常显式消费并清理状态 | TTL/重载持久化、同步/异步 8 路并发单次 fetch、WS 生命周期专项 |
| `infrastructure/httpx_pool.py`、`infrastructure/browser_pool.py`、`skills/templates/builtin-web/.../tools.py` | 动态技能不再持有逐调用 HTTP 客户端或孤立 Playwright driver；HTTPX 按事件循环复用有界连接池，关闭失效 loop 池；browser + driver 在空闲、失败与 shutdown 成对关闭，热重载不丢所有权 | 50 次获取 20.6s→0.41s；复用/关闭/失效 loop、browser 创建失败清理、Web handler 双调用专项 |
| `engine/init.py` | `builtin-web` 已安装副本只在内容精确匹配任一历史官方 Git blob 时原子升级；任意用户定制均保留，使既有安装实际获得新连接池而不覆盖自定义技能 | 官方历史副本 `c03e2a…`→当前模板 `d35f08…`；定制文件保留测试；当前工作区 4 个 Web 工具加载成功 |
| `feishu/lark_client.py`、`feishu/drive_client.py` | Lark SDK 缓存增加线程安全、密钥轮换识别、同 app 原子替换和 8 项 LRU；drive HTTP shutdown 同时清理 token、同步/异步锁表和 SDK 缓存 | 8 线程并发只构建一次、密钥轮换、上限与飞书发送/Drive 回归 |
| `tools/html_upload.py`、`engine/shutdown.py` | HTML 上传/列表/清理按事件循环复用 aiohttp 连接池，统一关停时显式关闭；URL、认证、超时和响应契约不变 | 连接池复用/关闭、统一 shutdown 资源断言 |
| `feishu/docx/blocks.py`、`feishu/poll_server.py` | 带 stats 文档追加复用一次 Markdown AST 结果；独立思考与反思卡改走异步发送 | parse 调用次数断言；Docx fallback、反思卡、merge-tools 回归 |
| `engine/thinking.py`、`feishu/poll_server.py` | 记录最后一次成功送达的思考卡 JSON；流式重复回调、重要内容重复判断和 finalize 内容未变化时不再发送相同 PATCH，也不消耗预算；失败不标记，后续仍可恢复 | 相同正文零 PATCH、失败后重试、finalize 去重及 72 项卡片/思考回归 |
| `feishu/ws_health.py` | supervisor 取消、shutdown、watchdog 与 receive cleanup 统一取消并消费异常；无 receive task 也断连；收包任务取消清理抛异常不再打断健康原因和外层重连 | 取消后收包任务终止/断连、清理异常仍返回 shutdown、43 项 WS/生命周期回归 |
| `engine/shutdown.py` | 会话状态、记忆索引、Trace join/清理、提案、锁和实例注册等同步边界移出事件循环，资源关闭顺序不变 | shutdown 顺序/幂等/heartbeat 专项 63 项 |
| `types/__init__.py` | eager 类型聚合改为缓存式惰性导出 | 导出兼容测试；冷启动基线 |
| `types/tool.py`、`memory/context.py`、`infrastructure/registry.py` | OpenAI schema 仅用于静态检查，运行时使用等价 dict 注解，避免加载 SDK 全部类型树 | `types.config` 导入不含 `openai`；全量测试 |
| `infrastructure/json_config.py` | 新增只在内存中生效的隔离 overlay，不写用户配置 | 配置文件不变测试 |
| `scripts/perf_trace_real_api.py` | 迁移到当前组合根；隔离状态；完整资源关闭；安全阶段报告和请求/响应配对 | mock 脚本测试；两次真实 API 验证 |

## 待审查队列

按风险与实测收益排序，逐批推进：

1. `tools/*` 剩余 schema 体积、工具并发与大结果内存路径。
2. `feishu/*` 剩余卡片出站去重与 WebSocket 极端异常路径。
3. `session/*` 剩余多线程列表/创建交错与 LRU 正在执行会话边界。
4. `engine/*` 剩余启动、命令分发和信号/异常关停路径。
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
