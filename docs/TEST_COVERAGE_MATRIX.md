# Mini Agent Python — 测试覆盖矩阵

> Mini Agent Python | 版本: 2.1.0 | 最后更新: 2026-07-11 | 与 `miniagent.__version__` 对齐

本文档记录 Mini Agent Python 项目的功能模块与测试文件的对应关系。

---

## 1. 核心模块 (core/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| Agent编排 | agent.py | test_engine_engine.py, test_run_agent_phases.py | ✅ 充足 |
| 执行器 | executor.py | test_executor_execute_plan.py, test_executor_system_prompt.py | ✅ 充足 |
| 规划器 | planner.py | test_planner_thinking_step.py, test_planner_full_flow.py | ✅ 充足 |
| 规划最小路径 | planner.py | test_planner_normalization.py | ✅ 充足 |
| 任务分类 | task_classifier.py | test_task_classifier_unit.py | ✅ 充足 |
| 需求澄清 | requirement_clarifier.py | test_requirement_clarifier.py | ✅ 充足 |
| 需求自澄清 | requirement_clarifier.py, memory/ground_truth.py | test_requirement_clarifier.py, test_memory_ground_truth.py | ✅ 充足 |
| 反思评估 | problem_solver.py | 间接测试 | ⚠️ 部分 |
| 配置管理 | config.py | test_merge_agent_config.py | ✅ 充足 |
| OpenAI客户端 | openai_client.py | test_openai_client.py | ✅ 充足 |
| Thinking预设 | thinking_presets.py | test_thinking_system.py | ✅ 充足 |
| Thinking回调 | thinking_callback.py | test_thinking_system.py | ✅ 充足 |
| 确认通道 | confirmation_channel.py | test_engine_engine.py | ✅ 充足 |
| LLM参数 | llm_params.py | test_model_config_env_thinking.py | ✅ 充足 |
| 自我优化 | self_opt/ | test_self_opt_types.py, test_self_opt_impl.py | ✅ 充足 |

---

## 2. 引擎模块 (engine/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 统一引擎 | engine.py | test_engine_engine.py | ✅ 新增 |
| 主入口 | main.py | test_startup.py, test_main_stop_args.py, test_cli_transcript_scroll.py | ✅ 充足 |
| 子系统初始化 | init.py | test_init_subsystems_registry.py | ✅ 充足 |
| 命令调度 | command_dispatch.py | test_command_dispatch.py | ✅ 新增 |
| CLI命令 | cli_commands.py | test_command_dispatch.py | ✅ 充足 |
| CLI transcript | cli_transcript.py | test_cli_transcript.py, test_cli_transcript_scroll.py | ✅ 充足 |
| Thinking显示 | thinking.py | test_thinking_system.py | ✅ 合并 |
| 后台任务 | background_tasks.py | test_background_tasks.py, test_btw_cmd.py | ✅ 充足 |
| 会话锁 | session_lock.py | test_session_lock.py | ✅ 充足 |
| 关闭流程 | shutdown.py | test_shutdown_lifecycle.py | ✅ 充足 |

---

## 3. 类型模块 (types/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 工具类型 | tool.py | test_types_tool.py | ✅ 充足 |
| 配置类型 | config.py | test_merge_agent_config.py | ✅ 充足 |
| 记忆类型 | memory.py | test_types_memory.py | ✅ 充足 |
| 确定事实类型 | memory.py | test_types_memory.py | ✅ 充足 |
| 规划类型 | planning.py | test_planner_thinking_step.py | ✅ 充足 |
| 确认类型 | confirmation.py | 间接测试 | ⚠️ 部分 |

---

## 4. 工具模块 (tools/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 文件系统 | filesystem.py | test_tools_filesystem.py | ✅ 充足 |
| 文件分析 RAG 入库 | filesystem.py, knowledge/file_ingest.py | test_tools_filesystem.py, test_knowledge_file_ingest.py | ✅ 充足 |
| 命令执行 | exec.py | test_tools_exec.py | ✅ 新增 |
| 核心工具 | core_tools.py | test_web_error_handling.py | ✅ 充足 |
| 技能工具 | skills.py (含 check_app) | test_tools_skills_clawhub.py | ✅ 充足 |
| 数据处理 | data_tools.py | test_data_tools_edge_cases.py | ✅ 充足 |
| 知识库 | knowledge_tools.py | test_knowledge.py | ✅ 充足 |
| 定时任务 | schedule_tools.py | test_schedule_tools.py | ✅ 充足 |
| 飞书IM | feishu_im_tools.py | test_feishu_im_tools_handlers.py | ✅ 充足 |
| 飞书文档 | feishu_doc_tools.py | test_feishu_doc_tools.py | ✅ 充足 |
| 飞书多维表格 | feishu_bitable_tools.py | test_feishu_bitable_tools.py | ✅ 充足 |

---

## 5. 飞书模块 (feishu/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| WebSocket客户端 | ws_client.py | test_feishu_ws_client.py | ✅ 新增 |
| WebSocket健康 | ws_health.py | test_feishu_ws_client.py | ✅ 新增 |
| 轮询服务器 | poll_server.py | test_feishu_server.py | ⚠️ 部分 |
| Lark客户端 | lark_client.py | test_feishu_im_send_clients.py | ✅ 充足 |
| IM发送 | im_send.py | test_feishu_reply.py | ✅ 合并 |
| 卡片构建 | cards/builder.py | test_feishu_cards_builder.py | ✅ 充足 |
| 卡片提取 | cards/extract.py | test_feishu_cards_extract.py | ✅ 充足 |
| 卡片表格 | cards/table_v2.py | test_feishu_cards_table_v2.py | ✅ 充足 |
| 云文档 | docx/ | test_feishu_doc_tables_media.py | ✅ 充足 |

---

## 6. 记忆模块 (memory/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 存储器 | store.py | test_memory_store.py | ✅ 充足 |
| 上下文管理 | context.py | test_context_overflow.py | ✅ 充足 |
| 关键词索引 | keyword_index.py | test_keyword_index.py | ✅ 充足 |
| 记忆热路径性能 | store.py, keyword_index.py | test_perf_synthetic.py::S2/S6/S11/S12 | ✅ 充足 |
| 活动日志 | activity_log.py | test_activity_log.py | ✅ 充足 |
| 分层记忆 | layered_memory.py | test_layered_memory.py | ✅ 充足 |
| 历史归档 | history_archive.py | test_memory_history.py | ✅ 合并 |
| 历史压缩 | history_progressive.py | test_memory_history.py | ✅ 合并 |
| 历史桥接 | history_bridge.py | test_memory_history.py | ✅ 合并 |

---

## 7. 基础设施 (infrastructure/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 工具注册表 | registry.py | test_registry.py | ✅ 充足 |
| 工具监控 | monitor.py | test_monitor.py | ✅ 充足 |
| 循环检测 | loop_detector.py | test_loop_detector.py | ✅ 充足 |
| 实例管理 | instance.py | test_instance_manager.py | ✅ 充足 |
| 消息队列 | message_queue.py | test_message_queue_abort.py | ✅ 充足 |
| 通道路由 | channel_router.py | test_channel_router_persist.py | ✅ 充足 |
| Trace钩子/写入 | tracing.py | test_tracing.py, test_trace_performance.py | ✅ 充足 |
| Trace统计/清理 | trace_stats.py | test_self_opt_integration.py | ✅ 充足 |

---

## 8. 技能模块 (skills/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 注册表 | registry.py | test_skills.py | ✅ 充足 |
| 加载器 | loader.py | test_skill_loader_metadata.py | ✅ 充足 |
| 刷新 | refresh.py | test_skill_refresh.py | ✅ 充足 |
| 快照 | snapshots.py | test_skills_snapshots.py | ✅ 充足 |
| 监视 | watch.py | test_skills_watch_shutdown.py | ✅ 充足 |

---

## 9. 定时任务 (scheduled_tasks/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 任务管理 | store.py | test_scheduled_tasks.py | ✅ 充足 |
| 调度循环 | ticker.py | test_scheduled_tasks_ticker.py | ✅ 充足 |
| Cron处理 | cron.py | test_scheduled_tasks_cron.py | ✅ 充足 |
| 任务执行 | runner.py | test_scheduled_tasks_runner.py | ✅ 充足 |
| 锁机制 | lock.py | test_scheduled_tasks_lock.py | ✅ 充足 |
| 飞书投递 | feishu_delivery.py | test_scheduled_tasks_feishu.py | ✅ 充足 |

---

## 10. 安全模块 (security/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 沙箱 | sandbox.py | test_sandbox.py, test_tools_exec.py | ✅ 充足 |

---

## 11. 会话模块 (session/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 管理器 | manager.py | test_session.py, test_session_history_sync.py | ✅ 充足 |
| 工作空间 | workspace.py | test_session_workspace_wiring.py | ✅ 充足 |

---

## 12. 知识库 (knowledge/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 基础 | base.py | test_knowledge.py | ✅ 充足 |
| 注册表 | registry.py | test_knowledge.py | ✅ 充足 |
| 自动文件入库 | file_ingest.py | test_knowledge_file_ingest.py | ✅ 充足 |

---

## 13. MCP (mcp/)

| 模块 | 文件 | 测试文件 | 覆盖状态 |
|------|------|----------|----------|
| 桥接 | bridge.py | test_mcp_bridge.py | ✅ 充足 |
| 运行时 | runtime.py | test_mcp_runtime.py | ✅ 充足 |

---

## 测试统计摘要

| 类别 | 说明 |
|------|------|
| **测试数量** | 以 `pytest tests/ --collect-only -q` 收集结果为准（勿硬编码） |
| **覆盖率目标** | 见 [INDEX.md](INDEX.md) §测试与质量 |

收集与覆盖率命令以 [INDEX.md](INDEX.md) §测试与质量 为准（勿在多处硬编码 pytest 块）。

---

## 维护说明

本矩阵只记录当前仓库真实存在的测试文件与主要覆盖关系，不保留某次重构过程中的“新增/合并”流水账。**新增、重命名或合并 `tests/test_*.py` 时须同步更新本页。** 删除测试文件时同样更新。

覆盖状态是人工维护的质量索引，不替代实时覆盖率报告。需要精确覆盖率时，以 `pytest --cov=miniagent` 输出为准。

---

## 运行测试

与 CI 一致的通用命令见 [INDEX.md](INDEX.md) §测试与质量。以下为**按模块回归**示例子集：

```bash
# 重点子集示例
pytest tests/test_engine_engine.py tests/test_command_dispatch.py \
  tests/test_feishu_ws_client.py tests/test_tools_exec.py -v

# CLI transcript / 历史加载回归
pytest tests/test_cli_transcript.py tests/test_cli_transcript_scroll.py \
  tests/test_session_history_sync.py -v

# Trace / 性能回归
pytest tests/test_tracing.py tests/test_trace_performance.py \
  tests/test_self_opt_integration.py::TestTraceStats tests/test_perf_synthetic.py -v

# 业务热路径性能回归
pytest tests/test_memory_store.py tests/test_keyword_index.py tests/test_perf_synthetic.py -q -m perf --durations=13

# 真实 API 压测（显式门禁，默认不联网）
set MINIAGENT_REAL_API_STRESS=1
pytest tests/evaluation/test_perf_real_api.py -v -s
```

