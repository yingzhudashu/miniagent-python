# 性能与 Trace 逐文件审计台账

> 状态：进行中 | 最近更新：2026-07-12

本文是性能增强工作的可验证台账，不替代架构或用户文档。每一项审查均记录：运行路径、时间/空间复杂度、同步 I/O、资源所有权、并发边界、Trace 可观测性、敏感数据边界、兼容性和回归证据。只有经过代码审查、自动测试和适用的真实 API 验证后，条目才标记为“已验证”。

## 验收基线

| 指标 | 优化前 | 当前证据 | 状态 |
|---|---:|---:|---|
| 合成性能测试 | 18 passed / 约 0.81s | 22 passed / 约 0.89s | 通过 |
| 合成 tracemalloc 峰值 | 44.21MiB | 21.18MiB | 约下降 52% |
| `engine.main` 冷导入 | 4.75s / 45.05MiB | 0.79s / 17.74MiB | 时间约下降 83%，峰值约下降 61% |
| 明确无工具的真实请求 | 17.32s；exec 输入 9,884 token | 12.77s；exec 输入 6,610 token | 耗时约下降 26%，exec token 约下降 33% |
| 真实规划工具闭环 | 无可靠 harness | 47.72s；2 次 `read_file` 均成功；26/26 Trace 写入 | 通过 |
| 非 evaluation 全量测试 | 既有基线 | 2379 passed，3 skipped，6 deselected | 通过 |

真实性能数字受网络与上游调度影响，只用于同机、同提示、同配置的方向性对比；功能正确性由测试、Trace 配对和工具结果共同验证。

## 已审查文件

| 文件 | 审查结论与改动 | 证据 |
|---|---|---|
| `infrastructure/tracing.py` | 修复 10ms 空轮询、低流量逐事件 flush、满队列关闭丢事件、大批次关闭等待、序列化静默丢失；紧凑 JSON；最终 writer 统计可返回 | Trace 专项测试；真实运行 10/10 与 26/26 写入，零丢弃/错误 |
| `infrastructure/trace_stats.py` | 同时读取基础/PID 分片；统一 Chat 与 Responses usage；缓存/推理 token、错误率、message/tool 数、平均/p50/p95 时延和分阶段统计 | `test_self_opt_integration.py`；真实请求/响应零失配 |
| `core/task_classifier.py` | 每次尝试记录安全 duration、message/tool 数；保留既有有界恢复 | 分类专项与真实 API（可恢复失败可见） |
| `core/llm_json.py` | JSON 控制阶段记录逐尝试 duration 和请求规模 | JSON/反思专项与真实反思请求 |
| `core/planner.py` | 规划尝试记录 duration 和请求规模，失败与恢复可分辨 | planner 专项；真实规划一次恢复后成功 |
| `core/executor.py` | 执行尝试记录 duration、attempt、model、usage；支持计划明确关闭工具；OpenAI 类型改为静态导入 | executor 专项；真实无工具与双文件工具闭环 |
| `core/agent.py` | 区分用户明确禁用工具与空工具箱；一般简单任务工具能力不变 | 无工具真实对比；Agent/全量测试 |
| `types/planning.py` | `tools_enabled` 建立无工具语义，避免复用 `required_toolboxes=[]` 的“不筛选”含义 | 工具选择单元测试 |
| `engine/__init__.py` | eager 聚合改为缓存式惰性导出，保留可选 `ThinkingDisplay=None` 兼容 | 全新进程导入测试；冷启动基线 |
| `memory/__init__.py` | eager 聚合改为缓存式惰性导出，消除 core↔memory↔engine 循环导入 | `import miniagent.core.agent` 全新进程成功 |
| `types/__init__.py` | eager 类型聚合改为缓存式惰性导出 | 导出兼容测试；冷启动基线 |
| `types/tool.py`、`memory/context.py`、`infrastructure/registry.py` | OpenAI schema 仅用于静态检查，运行时使用等价 dict 注解，避免加载 SDK 全部类型树 | `types.config` 导入不含 `openai`；全量测试 |
| `infrastructure/json_config.py` | 新增只在内存中生效的隔离 overlay，不写用户配置 | 配置文件不变测试 |
| `scripts/perf_trace_real_api.py` | 迁移到当前组合根；隔离状态；完整资源关闭；安全阶段报告和请求/响应配对 | mock 脚本测试；两次真实 API 验证 |

## 待审查队列

按风险与实测收益排序，逐批推进：

1. `trace_stats.py` 大文件流式聚合与 `remove_session_from_trace_files()` 的峰值内存、并发写安全。
2. `memory/store.py`、`shared_registry.py`、`keyword_index.py`、`embedding_search.py` 的缓存上限、批量持久化与锁粒度。
3. `memory/context.py`、`history_bridge.py`、`memory_context_service.py` 的上下文复制、重复序列化与文件结果保留策略。
4. `tools/*` 与 `infrastructure/registry.py` 的 schema 体积、工具箱选择和工具并发路径。
5. `feishu/*` 的 Markdown/卡片渲染缓存、同步 SDK 边界、长连接资源回收。
6. `engine/*` 的启动、会话、消息队列、后台任务及关停路径。
7. 其余 `miniagent/`、`scripts/` 与关键测试文件；逐项确认无无界缓存、阻塞 async I/O、重复解析或资源泄漏。

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
