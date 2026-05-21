# Mini Agent Python — 文档索引

> 📅 最后更新: 2026-05-20 | 版本: 2.0.2（与 `miniagent.__version__` 对齐；未发版行为以 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` 为准）

---

## 📖 快速开始

**阅读路径**

| 角色 | 建议顺序 |
|------|----------|
| 新用户 | [USER_GUIDE.md](USER_GUIDE.md) → [README](../README.md) 快速开始 → [CLI.md](CLI.md) |
| 开发者 | 本页目录树 → [ARCHITECTURE.md](ARCHITECTURE.md) → 专题文档 |
| 维护者 | [ENGINEERING.md](ENGINEERING.md) §5 清单 → [CHANGELOG](../CHANGELOG.md) |

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| **[USER_GUIDE.md](USER_GUIDE.md)** | **零基础全项目使用指南**（安装、配置、日常、FAQ、安全） | **所有用户；新手首选** |
| [README](../README.md) | 项目介绍、快速上手 | 所有用户 |
| [CHANGELOG](../CHANGELOG.md) | 版本变更记录 | 所有用户 |

## 🏗️ 架构文档

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构总览（含定时任务子系统；用户操作见 [USER_GUIDE.md](USER_GUIDE.md) §8） | 开发者、架构师 |
| [architecture.drawio](architecture.drawio) | 架构图 (draw.io) | 开发者 |
| [CHANNEL_BINDING.md](CHANNEL_BINDING.md) | 通道绑定功能详解 | 高级用户、开发者 |
| [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) | 三层记忆系统详解 | 开发者 |
| [SECURITY.md](SECURITY.md) | 安全模型说明 | 运维、开发者 |
| [CYBERNETICS_PLAN.md](CYBERNETICS_PLAN.md) | 控制论/自适应路线（**规划稿，非实现规格**） | 架构师 |

## 📦 模块文档

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [CLI.md](CLI.md) | CLI 命令手册 | 所有用户 |
| [FEISHU.md](FEISHU.md) | 飞书集成（含 §调研与路线图：卡片 JSON v2） | 运维、开发者 |
| [SELF_OPT.md](SELF_OPT.md) | 自我优化子系统 | 高级用户、开发者 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署指南 | 运维 |
| [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md) | 多实例注册表与磁盘布局 | 开发者、运维 |

## 🤝 参与贡献

| 文档 | 说明 | 适合读者 |
|------|------|----------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南、开发规范 | 贡献者 |
| [ENGINEERING.md](ENGINEERING.md) | 仓库卫生、质量门禁、单一事实来源 | 维护者、CI 负责人 |
| [PERFORMANCE.md](PERFORMANCE.md) | 性能 KPI 分层、合成基准、剖析命令、基线格式与 JSON 对比 | 维护者 |
| [docstring_inventory.md](docstring_inventory.md) | 缺失 docstring 扫描报告（`scripts/docstring_inventory.py` 生成） | 维护者 |

### 可选：离线测评（本地）

评测脚本与用例 JSON 位于 `tests/evaluation/`，**源码与小体积 JSON 宜纳入 Git**；运行方式与产物边界见 [EVALUATION_LOCAL.md](EVALUATION_LOCAL.md)。默认 CI 使用 `pytest -m "not evaluation"`；**轨迹与生成报告不入库**（见根目录 `.gitignore`），且勿 `git add -f` 轨迹以免泄漏对话中的密钥。

---

## 📁 项目结构

**权威目录树（与仓库同步）**；README 中仅为缩略指引，详见本段。

```
miniagent-python/
├── miniagent/
│   ├── __main__.py               # 统一入口（.env、--stop → compat.unified_entry）
│   ├── __init__.py               # 包版本号 __version__
│   ├── compat.py                 # 聚合导出与 unified_entry
│   ├── runtime/
│   │   ├── context.py            # RuntimeContext 组合根
│   ├── cli/
│   │   └── cli.py                # console_scripts 入口 → __main__
│   ├── core/
│   │   ├── agent.py              # 两阶段编排入口 run_agent / run_pipeline
│   │   ├── planner.py            # Phase 1 结构化规划
│   │   ├── executor.py           # Phase 2 ReAct / 分步执行
│   │   ├── config.py             # AgentConfig、MODEL_PROFILES
│   │   ├── request_payload.py    # 请求体构建辅助（与执行器/通道对齐）
│   │   ├── openai_client.py      # 共享 AsyncOpenAI
│   │   ├── openai_message_sanitize.py
│   │   ├── thinking_callback.py  # 思考流回调适配
│   │   ├── llm_params.py         # 完成参数与 thinking 合并
│   │   ├── thinking_presets.py   # 业务深度 → 档位映射
│   │   ├── task_classifier.py    # 任务难度预分类
│   │   ├── vendor/qwen_extra.py  # DashScope/Qwen extra_body
│   │   └── self_opt/             # 自我优化（inspector、proposal、git 等）
│   ├── engine/
│   │   ├── main.py               # unified_main、CLI 主循环
│   │   ├── engine.py             # UnifiedEngine
│   │   ├── init.py               # 内置工具、技能、MCP、SessionManager
│   │   ├── builtin_tools.py      # ALL_TOOLS 注册
│   │   ├── command_dispatch.py   # `.` 命令调度
│   │   ├── cli_commands.py       # 命令实现
│   │   ├── cli_state.py          # CliLoopState 与主循环状态对齐
│   │   ├── feishu_state.py       # FeishuRuntime
│   │   ├── feishu_runtime.py     # 兼容别名：重导出 FeishuRuntime
│   │   ├── shutdown.py           # 进程关停与资源释放编排
│   │   ├── session_lock.py
│   │   ├── thinking.py           # ThinkingDisplay
│   │   ├── markdown_cli.py       # CLI Markdown / ANSI 辅助
│   │   └── welcome.py
│   ├── scheduled_tasks/          # 定时任务：持久化 + 进程内 ticker → 消息队列跑 Agent
│   │   ├── __init__.py
│   │   ├── models.py             # ScheduledTask / ScheduleSpec
│   │   ├── store.py              # tasks.json 读写
│   │   ├── cron.py               # cron 下一触发时间（croniter）
│   │   ├── timezone_util.py      # 定时任务新建默认时区（MINIAGENT_SCHEDULE_TIMEZONE）
│   │   ├── feishu_delivery.py    # primary 镜像飞书投递与消息队列 chat_id 对齐
│   │   ├── file_lock.py          # tasks.json / job 跨进程锁
│   │   ├── lock.py               # scheduler.lock（tick 互斥）
│   │   ├── ticker.py             # 周期 tick、投递协程
│   │   ├── runner.py             # build_run_scheduled_job_coro → UnifiedEngine
│   │   └── resolve.py            # 执行目标 session_key / 飞书判定
│   ├── feishu/
│   │   ├── poll_server.py        # WebSocket 长连接、事件分发、与消息队列衔接
│   │   ├── ws_client.py          # lark WS 客户端包装（收包 task / connected）
│   │   ├── ws_health.py          # 看门狗与会话监督（死连接/空闲刷新）
│   │   ├── agent_handler.py
│   │   ├── agent_channel_prompts.py
│   │   ├── server.py
│   │   ├── types.py
│   │   ├── resource_io.py        # 飞书媒体/资源下载与会话落盘
│   │   ├── im_send.py            # IM 发送客户端封装
│   │   ├── im_tool_policy.py     # 内置飞书工具策略
│   │   ├── lark_response.py
│   │   ├── docx_client.py
│   │   ├── docx_blocks.py
│   │   ├── drive_client.py
│   │   ├── folder_token_resolve.py  # 云盘文件夹 URL / token 解析
│   │   └── upload_io.py
│   ├── infrastructure/
│   │   ├── registry.py           # ToolRegistry
│   │   ├── monitor.py
│   │   ├── message_queue.py
│   │   ├── timezone_config.py    # process_timezone / Agent 本地时间注入
│   │   ├── env_loader.py         # 加载项目根 .env
│   │   ├── env_parse.py          # env_flag / env_flag_strict / 遗留别名读取
│   │   ├── channel_router.py
│   │   ├── instance.py
│   │   ├── feishu_inbound_lock.py
│   │   ├── tracing.py
│   │   ├── logger.py
│   │   ├── loop_detector.py
│   │   ├── process.py
│   │   └── debug_ndjson.py       # 可选 NDJSON 调试落盘
│   ├── memory/
│   │   ├── defaults.py           # 进程默认记忆 bundle / MINI_AGENT_STATE
│   │   ├── context.py            # 消息窗口、压缩、记忆注入
│   │   ├── store.py
│   │   ├── activity_log.py
│   │   ├── keyword_index.py
│   │   ├── memory_pipeline.py    # 记忆注入管线
│   │   ├── history_archive.py
│   │   ├── history_bridge.py
│   │   ├── history_progressive.py
│   │   ├── layered_memory.py
│   │   └── dream_scheduler.py
│   ├── session/
│   │   ├── manager.py
│   │   └── workspace.py
│   ├── skills/
│   │   ├── registry.py
│   │   ├── loader.py
│   │   ├── paths.py              # 技能根目录解析
│   │   ├── builtin_toolboxes.py
│   │   └── clawhub_client.py
│   ├── tools/
│   │   ├── exec.py
│   │   ├── filesystem.py
│   │   ├── web.py
│   │   ├── skills.py
│   │   ├── self_opt.py
│   │   ├── git_readonly.py
│   │   ├── cli_dispatch_tools.py # run_dot_command（点命令同源）
│   │   ├── schedule_tools.py     # manage_scheduled_task（结构化 CRUD）
│   │   ├── feishu_im_tools.py    # 可选 extra「feishu」：云文档/IM 等内置工具
│   │   └── session_memory.py     # 会话记忆类工具（init 注册）
│   ├── mcp/                      # 可选：stdio MCP → mcp_* 工具
│   │   ├── bridge.py
│   │   └── runtime.py
│   ├── security/
│   │   └── sandbox.py
│   └── types/                    # Pydantic / Protocol（agent、config、tool、planning、memory、skill；__init__ 聚合导出）
├── scripts/                      # 维护脚本（bootstrap_clawhub_skills.py、vendor_skill_from_github.py、compare_perf_snapshots.py、perf_profile_tracemalloc.py 等）
├── tests/                        # pytest 主收集根；可选子目录 evaluation/ 见 EVALUATION_LOCAL.md
├── docs/
│   └── examples/                 # 脱敏配置片段（见 examples/README.md）
├── workspaces/                   # 默认状态目录（可用 MINI_AGENT_STATE 迁出）
│   └── skills/                   # 技能包根（内置 skill-creator、skill-vetter；第三方见 [workspaces/skills/THIRD_PARTY_SKILLS.md](../workspaces/skills/THIRD_PARTY_SKILLS.md)）
└── README.md
```

**`workspaces/` 与 Git**：`.gitignore` 已忽略 `instances/`、`sessions/`、`memory/`、`scheduled_tasks/`（定时任务表）、`keyword-index.json`、`feishu/`、性能日志、飞书路由 JSON 等运行时产物；结构说明见 [ENGINEERING.md](ENGINEERING.md) §3.1。本地与 CI 推荐设置 `MINI_AGENT_STATE` 将状态迁出仓库；需要提交**脱敏**结构示例时请放在 [examples/](examples/)（说明见 [examples/README.md](examples/README.md)），勿将含密钥或真实对话的文件放在 `workspaces/` 并尝试提交。

---

## 🔗 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
