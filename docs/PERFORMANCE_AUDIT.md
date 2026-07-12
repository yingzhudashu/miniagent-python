# 性能与 Trace 逐文件审计台账

> 状态：本轮实施与验收完成 | 最近更新：2026-07-12

本文是性能增强工作的可验证台账，不替代架构或用户文档。每一项审查均记录：运行路径、时间/空间复杂度、同步 I/O、资源所有权、并发边界、Trace 可观测性、敏感数据边界、兼容性和回归证据。只有经过代码审查、自动测试和适用的真实 API 验证后，条目才标记为“已验证”。

## 验收基线

| 指标 | 优化前 | 当前证据 | 状态 |
|---|---:|---:|---|
| 合成性能、Trace、惰性导入与资源生命周期专项 | 18 passed / 约 0.81s | 95 passed / 15.74s（覆盖项显著增加） | 通过 |
| 合成 tracemalloc 峰值 | 44.21MiB | 约 16.40MiB | 约下降 62.9% |
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
| 本轮非 evaluation 全量测试 | 2449 passed，3 skipped | 2470 passed，3 skipped，6 deselected | 通过 |
| 本轮合成/Trace 性能专项 | 14 passed | 42 passed / 3.18s | 通过 |
| 同机、同配置、同提示真实工具闭环 | 46.59s（单次） | 39.97 / 31.33 / 27.22s；中位数 31.33s | 中位数下降 32.8% |
| 完整真实 API 矩阵 | 无 | 纯回复中位 6.66s；单工具 18.44s；多工具 58.89s；3 并发中位 6.91s | 12/12 通过 |
| 真实 Trace 完整性 | 28/28；10/10 LLM 配对 | 1357/1357；55/55 LLM 配对；9/9 工具成功；秘密命中 0 | 通过 |
| 长矩阵预热后平台 | 未记录 | RSS 中位平台 +0.14%；Python traced 平台 +0.41% | 通过（<10%） |
| 真实 embedding | 2 次 API，平均约 8.3s；含空索引查询 | 空索引 0 API；有效 query/index 中位约 202ms；index 有界异步排队 | 通过 |

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
| `infrastructure/tracing.py`、`trace_events.py` | 增加 agent/phase/resource/embedding 生命周期事件、`call_id`、span 继承、250ms 可选资源采样、UTC 跨日轮转和 writer 重启/慢关闭保护；`metrics_only` 改为字符串与标量双白名单，未知字段不落盘 | 跨日、自定义路径轮转、复用、未知字符串/数字秘密阻断、并发 hook；真实 1357/1357 写入且秘密命中 0 |
| `infrastructure/trace_stats.py` | 延迟 reservoir、session bitmap、phase/tool/error/span 分组基数均有硬上限；坏 JSON、NaN、错误类型对象及未知事件容错；资源、span 和采样标记进入报告 | 高基数 15000 事件专项；长矩阵 1026 个资源样本；RSS 平台 +0.14% |
| `core/llm_transport.py` | 按弱引用 client + endpoint + model + wire API 学习确定性 400 不支持参数；每 client 与 fallback 均为 LRU；仅匹配明确“不支持参数”措辞，非法参数值不会误学习 | 参数只失败一次、endpoint 隔离、合法端点保持参数专项；真实运行无不支持参数重复失败 |
| `memory/embedding_search.py`、`store.py` | 空索引在生成 query vector 前返回；index 使用容量 256/并发 2 的有界任务队列，满载反压、不丢数据、搜索/关闭 drain；并发 drain 等待同一任务；embedding 记录 purpose、缓存、队列/网络/索引时延和安全失败类别；会话 JSON 原子替换 | 空索引零调用、single-flight、取消、并发 drain、读后写一致；真实 12 queued/12 completed |
| `memory/context.py` | `set_tools()` 只失效工具预算；token cache 使用完整摘要、单调 TTL 和 RLock，固定 1000 项；消息顺序与压缩策略不变 | context/history/压缩全量回归与合成性能门禁 |
| `tools/filesystem.py`、`knowledge/file_ingest.py` | `read_file` 单遍计行/分页/完整 hash，正文只保留页与 RAG 上限；入库在线程执行；非递归和递归目录枚举用 O(max_entries) heap；写/编辑/镜像/元数据原子替换 | 大文件分页峰值、hash/行数、heartbeat、目录截断与旧文件保留专项；真实 read_file 6/6 |
| `tools/data_tools.py`、`tools/exec.py`、`infrastructure/atomic_json.py` | JSONL 逐行解析；CSV/JSON 参数与输出硬上限；CSV/JSON 写入先验证后原子替换；命令管道持续 drain 但只保留 1MB，超时/异常统一注销子进程 | 数据边界、固定输出预算、原子写入与 exec 全量回归 |
| `infrastructure/monitor.py`、`metrics.py`、`engine/markdown_cli.py` | 错误样本、延迟样本、Console 和渲染缓存固定容量；渲染同时受条目数/8MiB 限制；共享 Console 文件切换由 RLock 保护 | monitor/metrics/Markdown 专项与并发安全复核 |
| `bootstrap/application.py`、`memory/dream_scheduler.py`、`engine/shutdown.py` | fire-and-forget 任务终态异常被消费；shutdown 在最后一个 producer 停止后记录完整 span，Trace writer 最后 drain；embedding 和所有连接池有明确 owner | shutdown、Feishu、队列、任务取消与资源回收全量回归 |

## 全项目审查完成度

本轮台账覆盖 Git 跟踪的 275 个 `miniagent/**/*.py` 文件、8 个运行/维护脚本、默认配置、CI 门禁与 266 个测试文件。逐文件检查项统一为：复杂度与大输入边界、async 中同步 I/O、任务/锁/连接所有权、缓存容量、Trace 覆盖、敏感字段和关闭顺序。没有证据收益的文件标记为“已审查、无需修改”；有明确热点或边界问题的文件在上表逐项登记改动与验证证据。

| 目录 | 文件数 | 状态 | 重点结论 |
|---|---:|---|---|
| application / bootstrap / contracts / types | 32 | 已审查 | 组合根与协议保持兼容；任务终态异常消费；类型和架构门禁通过 |
| core | 39 | 已审查 | LLM 关联、能力缓存、规划/分类/执行/反思 span 完整；无质量降级 |
| engine | 42 | 已审查 | CLI/飞书同优先级；队列、后台任务、Markdown 缓存和 shutdown 所有权明确 |
| feishu | 41 | 已审查 | 未做真实写操作；卡片、连接、重连、去重与关闭由 Mock/故障测试覆盖 |
| infrastructure | 28 | 已审查 | Trace、统计、连接池、监控与原子写入完成容量/并发边界修复 |
| memory / knowledge | 20 | 已审查 | 空索引、异步 embedding、token/索引缓存、流式入库及原子持久化完成 |
| skills / MCP | 26 | 已审查 | 扫描/解析/安装移出事件循环；客户端和进程连接由统一 shutdown 回收 |
| tools | 19 | 已审查 | 大文件/JSONL/CSV/目录/命令输出有硬上限；写工具原子发布 |
| session / scheduled_tasks | 15 | 已审查 | session/lock/task 表有 LRU、TTL 或完成回收；持久化不阻塞事件循环 |
| cli / security / testing / utils / resources | 13 | 已审查 | 无新增无界状态；配置默认关闭采样并保持兼容 |

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
