# 系统架构

> Mini Agent Python | 版本: 2.0.1 | 架构图: [architecture.drawio](architecture.drawio)

## 架构总览

Mini Agent Python 采用 **两阶段架构**（Plan → Execute），通过 **ReAct 循环** 实现 LLM 驱动的智能代理。系统分为 11 个功能层，支持 CLI 和飞书双通道接入，并通过 **ChannelRouter** 实现通道绑定与会话共享。

```
                    用户输入
                 ┌────┴────┐
                CLI      飞书 WebSocket
                 └────┬────┘
                      ↓
        ┌─────── 入口层 (Entry) ──────┐
        │  __main__.py / compat.py    │
        └─────────────┬───────────────┘
                      ↓
        ┌─────── 引擎层 (Engine) ─────┐
        │  main.py: 生命周期管理       │
        │  engine.py: UnifiedEngine   │
        │  command_dispatch.py: 命令  │
        │  message_queue: 消息调度    │
        └─────────────┬───────────────┘
                      ↓
        ┌──── 通道路由层 (Router) ────┐
        │  channel_router.py          │
        │  CLI ↔ 飞书会话绑定/解绑     │
        │  session_key 解析            │
        └─────────────┬───────────────┘
                      ↓
        ┌─────── 核心层 (Core) ───────┐
        │  Phase 1: planner.py 规划   │
        │  Phase 2: executor.py 执行  │
        │  agent.py: 两阶段编排       │
        └───────┬─────┬───────────────┘
                ↓     ↓
         ┌──────┘     └──────┐
    工具层 (Tools)      记忆层 (Memory)
    exec / fs / web    store / context / index
         └──────┐     ┌──────┘
                ↓     ↓
        ┌─── 基础设施层 (Infra) ──────┐
        │  registry / monitor / logger │
        │  instance / process / loop   │
        └─────────────────────────────┘
                      ↓
        ┌─── 安全层 + 类型层 ─────────┐
        │  sandbox.py / types/*.py     │
        └─────────────────────────────┘
```

## 各层详细说明

### 1. 入口层 (Entry)

| 文件 | 职责 |
|------|------|
| `__main__.py` | 统一入口：`.env`、`--stop` 子命令，其余委托 `compat.unified_entry` |
| `compat.py` | 聚合导出与 `unified_entry`；构造 `RuntimeContext`（含 `get_shared_async_openai()`）后 `asyncio.run(unified_main)` |
| `core/openai_client.py` | 共享 `AsyncOpenAI` 惰性单例；测试可 `reset_shared_async_openai_for_tests()` |
| `runtime/context.py` | `RuntimeContext`：进程级 registry / monitor / skill_registry / clawhub / engine / channel_router / message_queue / feishu / memory_store / activity_log / keyword_index / openai_client（可选） |
| `cli/cli.py` | 控制台脚本 `miniagent` 的入口（委托 `__main__.main`） |

### 2. 引擎层 (Engine)

运行时编排层，管理整个 Agent 生命周期。

| 文件 | 职责 |
|------|------|
| `main.py` | 主启动入口：信号处理、CLI 主循环、同进程飞书连接启停、优雅关闭 |
| `engine.py` | `UnifiedEngine`：会话上下文管理、Agent 编排、思考回调、历史持久化 |
| `command_dispatch.py` | 统一命令调度器：CLI 和飞书共享 `.` 命令，输出捕获（StringIO） |
| `cli_commands.py` | CLI 命令实现：.session, .instance, .queue, .bind/.unbind, .help 等 |
| `feishu_state.py` | `FeishuRuntime`：飞书长轮询任务 start/stop/status（`feishu_runtime.py` 仅为同名兼容重导出） |
| `session_lock.py` | 会话级锁管理：PID 存活检测、跨实例互斥 |
| `thinking.py` | `ThinkingDisplay`：CLI 实时打印 / 飞书缓冲模式 |
| `init.py` | 子系统初始化：技能加载、SessionManager、默认会话 |
| `welcome.py` | 欢迎界面：版本号、会话信息 |

### 2b. 通道路由层 (Router)

| 文件 | 职责 |
|------|------|
| `channel_router.py` | `ChannelRouter`：通道-会话路由器，支持 CLI/飞书私聊绑定到同一主会话，群聊保持独立 |

通道路由层负责将不同输入通道映射到统一的主会话 ID：
- **CLI** (`__cli__`)：可绑定到任意会话，实现 CLI 干预飞书会话
- **飞书私聊** (`feishu_p2p:<sender_id>`)：可绑定到 CLI 会话，实现飞书消息共享
- **飞书群聊** (`feishu:<chat_id>`)：始终独立会话，不参与绑定

详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md)。

### 3. 核心层 (Core)

Agent 的大脑，实现两阶段架构。

| 文件 | 职责 |
|------|------|
| `agent.py` | 两阶段主入口：`run_agent()` (Plan→Execute), `run_pipeline()` (线性管线) |
| `planner.py` | Phase 1 规划器：LLM 分析需求 → 生成 `StructuredPlan` → 选择工具箱 → 估算 tokens |
| `executor.py` | Phase 2 执行器：ReAct 循环 (Think→Act→Observe) → 工具调用 → 记忆注入 → 活动日志 |
| `config.py` | 配置管理：`MODEL_PROFILES`, `AgentConfig` 合并, 循环检测默认值 |
| `self_opt/` | 自我优化子系统（详见 [SELF_OPT.md](SELF_OPT.md)） |

#### ReAct 循环详解

```
用户输入 → Planner 规划 → StructuredPlan
                              ↓
                        Executor 循环:
                    ┌──→ LLM 调用 (Think)
                    │       ↓
                    │   有工具调用? ──否──→ 返回最终回复
                    │       ↓ 是
                    │   执行工具 (Act)
                    │       ↓
                    │   结果反馈 (Observe)
                    │       ↓
                    │   循环检测
                    │       ↓
                    └── 继续循环 (max_turns)
```

### 4. 飞书层 (Feishu)

| 文件 | 职责 |
|------|------|
| `poll_server.py` | WebSocket 长轮询：WSClient 单例、内存+磁盘双重去重、消息防抖合并、优雅关闭 |
| `agent_handler.py` | 消息处理器：`create_feishu_handler()` → 飞书消息 → Agent → 回复 |
| `server.py` | HTTP Webhook（备选，需公网 IP） |
| `types.py` | `FeishuConfig`, `FeishuEvent` 类型定义 |

### 5. 记忆层 (Memory)

三层记忆架构，详见 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。

| 层 | 文件 | 说明 |
|---|------|------|
| Layer 1 | `store.py` | 短期记忆：会话级记忆存储、事实提取、摘要生成 |
| Layer 2 | `activity_log.py` | 活动日志：详细操作流水，写入 `memory/YYYY-MM-DD.md` |
| Layer 3 | `keyword_index.py` | 语义检索：TF-IDF 加权关键词索引，跨会话搜索 |
| 管理 | `context.py` | 上下文管理：Token 计数、自动压缩、记忆注入、消息窗口 |

### 6. 会话层 (Session)

| 文件 | 职责 |
|------|------|
| `manager.py` | `SessionManager`：创建/切换/重命名/列出会话，编号↔ID 双重解析，内存+磁盘双查找 |
| `workspace.py` | 工作空间管理：会话目录结构、config.json、history.jsonl |

### 7. 技能层 (Skills)

| 文件 | 职责 |
|------|------|
| `registry.py` | 技能注册表：注册/发现/状态管理 |
| `loader.py` | 技能加载器：动态导入、工具箱提取、Prompt 合并 |
| `clawhub_client.py` | ClawHub 客户端：技能搜索/安装/版本管理 |

### 8. 工具层 (Tools)

LLM 可通过 function calling 调用的工具：

| 文件 | 工具 |
|------|------|
| `exec.py` | 命令执行 (subprocess) |
| `filesystem.py` | 文件操作 (read/write/list/edit) |
| `web.py` | 网页访问 (search/fetch) |
| `skills.py` | 技能操作 (install/list) |
| `self_opt.py` | 自优化工具 (inspect/optimize) |

### 9. 基础设施层 (Infrastructure)

| 文件 | 职责 |
|------|------|
| `registry.py` | `ToolRegistry`：工具注册/查找/Schema 导出 |
| `monitor.py` | 性能监控器：耗时统计、成功率追踪 |
| `message_queue.py` | 消息队列：按 chat_id 隔离、queue/preemptive 双模式、耗时追踪 |
| `instance.py` | 多实例注册表：自增 ID、心跳、PID 存活检测、超时清理 |
| `logger.py` | 日志系统：`append_log()`, `get_logger()` |
| `loop_detector.py` | 循环检测器：相似度检测、warning/critical 分级 |
| `process.py` | 进程管理：子进程追踪、孤儿进程清理 |

### 10. 安全层 + 类型层

- **安全层** (`security/sandbox.py`): 路径白名单、父目录遍历拦截、权限策略
- **类型层** (`types/`): 7 个类型模块，定义 Agent、Config、Plan、Tool、Skill、Memory、Feishu 类型

## 数据流

### CLI 数据流

```
用户 stdin → run_cli_loop() → message_queue.dispatch_cli()
    → channel_router.resolve("__cli__")  ← 解析 session_key
    → _process_input() → engine.run_agent_with_thinking()
    → run_agent() → planner → executor (ReAct)
    → 工具调用 → LLM 回复 → stdout
```

**通道绑定影响**：当 CLI 通过 `.bind cli <会话>` 绑定到某会话时，
`channel_router.resolve("__cli__")` 返回绑定的 session_key，
CLI 输入将使用绑定的会话上下文（记忆/文件/工具共享）。

### 飞书数据流

```
飞书平台 → WSClient (WebSocket) → poll_server.on_message_receive()
    → 去重检查 → 防抖合并 → message_queue.dispatch_feishu()
    → handler() → 命令拦截 (.开头) 或 engine.run_agent_with_thinking()
        → channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
        → run_agent() → planner → executor (ReAct)
        → 工具调用 → LLM 回复 → 飞书 API 发送回复
```

**通道绑定影响**：飞书私聊消息通过 `resolve_feishu_message()` 解析：
- 若 `feishu_p2p:<sender_id>` 已绑定，则使用绑定的 session_key
- 群聊消息始终使用独立的 `feishu:<chat_id>` session_key

### 命令调度流（CLI + 飞书共享）

```
用户输入 ".status"
    ↓
command_dispatch.dispatch_command()
    ↓ capture=True (飞书) / False (CLI)
_format_status(state, message_queue)
    ↓
返回字符串 → 飞书回复 / CLI print
```

## 关键设计决策

### 为什么选择两阶段架构？

1. **Phase 1 (规划)**: LLM 先分析需求，选择合适的工具箱，估算资源消耗
2. **Phase 2 (执行)**: 只加载必要的工具，减少 token 浪费

好处：复杂任务可以精确规划，简单任务可以跳过规划直接执行。

### 消息队列设计

两种模式适配不同场景：
- **queue 模式**：按顺序处理，适合需要上下文连贯的场景
- **preemptive 模式**：最新消息优先，适合实时交互场景

按 `chat_id` 隔离队列，防止不同用户/群的消息互相干扰。

### 多实例设计

从单实例 PID 锁升级到多实例注册表：
- 支持多终端同时运行（CLI + 飞书）
- **新实例 `register()` 时**与 **`list_all()`** 均会按 **操作系统 PID 是否仍存在** 清理僵尸注册目录（不向其它 PID 发终止信号）；心跳文件仍写入，仅供观测，不作为存活权威判定（详见 [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md)）
- 会话级锁保证数据一致性

### 多会话并发安全

系统支持多会话并发运行，关键安全机制如下：

1. **按 chat_id 隔离队列**：`MessageQueueManager` 为每个 `chat_id` 维护独立队列，
   CLI 使用专用 `__cli__` 通道，飞书群/私聊各自隔离，消息互不干扰。

2. **思考计数器隔离**：`ThinkingDisplay` 为每个 `session_key` 独立维护思考计数器，
   多群并发发送消息时，各群的思考推送互不覆盖。

3. **通道绑定的双向回调**：当 CLI 绑定到飞书会话时，思考内容通过 `_dual_send`
   回调同时发送到终端和飞书，确保 CLI 用户能实时看到飞书会话的思考过程。

4. **SessionManager 会话锁**：每个会话工作空间有 `.lock` 文件记录持有者 PID，
   跨实例切换会话时自动检测锁冲突。

5. **session_manager 为唯一数据源**：`UnifiedEngine` 不再维护冗余的 `_feishu_sessions`
   字典，所有会话历史统一通过 `SessionManager` 管理，避免多源不一致。

## 运行时组合根

启动时由 `compat.unified_entry`（或等价入口）实例化 `RuntimeContext`（见 `runtime/context.py`），字段包括 `registry`、`monitor`、`skill_registry`、`clawhub`、`engine`，以及 **`channel_router`**、**`message_queue`**、**`feishu`**（`FeishuRuntime`），以及 **`memory_store`**、**`activity_log`**、**`keyword_index`**，以及 **`openai_client`**（入口通常设为 `get_shared_async_openai()`；为 `None` 时执行链回落共享工厂）。`unified_main`、CLI 主循环与飞书消息处理器通过该对象（或由其闭包捕获）获取依赖，避免在 `compat`/`unified` 等模块上维护可变全局。

`clawhub` 由入口注入并写入 `ToolContext`，技能工具优先使用 `ToolContext.clawhub`；必要时仍可调用 `create_clawhub_client()` 作为回退。

主循环状态字典与 **`CliLoopState`**（`engine/cli_state.py`）对齐，供 `dispatch_command` 与飞书 handler 共享。

## 已知技术债（进程级状态）

以下仍为进程级状态，测试多实例或并行连接时需注意：

- **`poll_server.py`**：飞书 SDK `WSClient` 按 appId 进程内复用（防止多连接事件路由不确定），与「每 `RuntimeContext` 一套依赖」正交。

记忆层默认实例已与入口同源（`miniagent.memory.defaults`）；`RuntimeContext.openai_client` 贯通 CLI / 飞书主路径；未设置时可回落到 `miniagent.core.openai_client.get_shared_async_openai()`（进程内单例）。

## 版本里程碑与待定清理（2.x）

- **顶层 `src` 兼容包**：已移除；唯一入口为 **`python -m miniagent`**（与 `pyproject.toml` 包发现 `miniagent*` 一致）。
- **弃用的记忆模块导出**：`miniagent.memory` 包级 `memory_store` / `activity_log` 及子模块惰性同名导出已于 **2.0.0** 移除，请使用 `get_process_default_memory_bundle()` 或 `RuntimeContext` 注入。
- **飞书 `poll_server` WSClient**：进程内按 appId 复用仍为有意设计；仅在同进程多 AppId / 测试隔离有硬需求时再重构（参见上文「已知技术债」）。

## 扩展点

| 扩展点 | 方式 | 说明 |
|--------|------|------|
| 添加工具 | `miniagent/tools/` 新增文件 | 实现 handler + register 函数 |
| 添加技能 | `workspaces/skills/` | manifest.yaml + Python 模块 |
| 添加命令 | `command_dispatch.py` | 注册新路由 |
| 自定义模型 | `.env` + `config.py` | 支持任何 OpenAI 兼容 API |
| 新通道 | 仿照 `feishu/` | 实现消息接收 + 回复发送 |
