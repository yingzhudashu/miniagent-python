# 系统架构

> Mini Agent Python | 版本: 2.0.3 | 架构图: [architecture.drawio](architecture.drawio)

## 架构总览

Mini Agent Python 采用 **多阶段架构**（Phase 0 分类 → Phase 0.5 需求澄清 → Phase 1 规划 → Phase 2 执行），通过 **ReAct 循环** 实现 LLM 驱动的智能代理。系统分为 **12 个功能层**（含可选 MCP 层），支持 CLI 和飞书双通道接入，并通过 **ChannelRouter** 实现通道绑定与会话共享。

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
        │  Phase 0.5: requirement_clarifier.py │
        │           需求澄清（三步法）  │
        │  Phase 1: planner.py 规划   │
        │  Phase 2: executor.py 执行  │
        │  agent.py: 多阶段编排       │
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

## 与 OpenClaw 的关系

- **OpenClaw**：自托管 **Gateway**，将 Discord、Telegram、飞书等多种渠道接到「口袋里的」Agent，强调多通道、会话隔离与控制中心 UI；官方文档见 [https://docs.openclaw.ai](https://docs.openclaw.ai)。
- **本仓库（Mini Agent Python）**：定位是 **Python Agent 核心**——多阶段架构（需求澄清→规划→执行）、ReAct、`ToolRegistry`、技能与 ClawHub、飞书与 CLI、本地记忆与工作空间。它**不是** OpenClaw Gateway 的等价实现，但可与同一生态（如 ClawHub 技能）对齐使用习惯。
- **可选 MCP**：`config.user.json` 中 `mcp.stdio_command` 设为 JSON 数组 `[command, arg1, ...]`（与 `stdio` 启动参数一致），进程启动时在 [`engine/init.py`](miniagent/engine/init.py) 中调用 [`register_mcp_stdio_tools`](miniagent/mcp/runtime.py) 连接 MCP 服务端并注册 `mcp_*` 工具；需安装可选依赖 `pip install miniagent-python[mcp]`。

### 配置（JSON 格式）

- **配置层级**：`config.defaults.json`（随代码发布）→ `config.user.json`（用户覆盖）；**不支持** `MINIAGENT_*` 环境变量或 `MINIAGENT_CONFIG` 覆盖。
- **敏感凭据**：API 密钥等放在 `config.user.json` 的 `secrets` 部分，由 [`env_loader.py`](miniagent/infrastructure/env_loader.py) 桥接到 `OPENAI_API_KEY` 等 SDK 环境变量（非用户配置入口）。
- **配置加载**：[`JsonConfigLoader`](miniagent/infrastructure/json_config.py) 统一管理；[`get_config`](miniagent/infrastructure/json_config.py) 支持点路径访问。Internal 常量见 [`core/constants.py`](miniagent/core/constants.py)。
- **嵌入调用**：若跳过 [`compat.unified_entry`](miniagent/compat.py) 而直接 [`unified_main`](miniagent/engine/main.py)，须先调用 `load_secrets_from_project_root()`。
- **任务难度预分类与规划可见输出**：Internal 常量 `EXECUTION_TASK_CLASSIFIER_ENABLED`（默认开启）；关闭则始终走完整规划。当 Internal 常量 `EXECUTION_ANNOUNCE_DIFFICULTY` 为真且存在 `on_thinking` 时，[`run_agent`](miniagent/core/agent.py) 将「评估中 → 难度结论 → 执行计划」合并为**同一条**流式思考，统一 header 为 **`[评估与计划]`**；展示为精简 Markdown，完整难度/计划正文经可选关键字参数 **`full_record`** 由 [`UnifiedEngine`](miniagent/engine/engine.py) 写入会话 `thinking` 历史。飞书侧由 `ThinkingDisplay` + `push_feishu_thinking_stream` PATCH 同一张交互卡；进入执行阶段时若 header 切换，则 **`finalize_only`** 收尾当前卡再开新段（见 [`thinking.py`](miniagent/engine/thinking.py) / [`engine._feishu_send`](miniagent/engine/engine.py)）。`EXECUTION_ANNOUNCE_DIFFICULTY=false`（Internal，见 `constants.py`）可关闭上述规划段推送。
- **分步执行**：Internal 常量 `PHASED_EXECUTION`（默认开启）、`STEP_MAX_TURNS`（默认 **48**）、`AGENT_MAX_TURNS`（默认 **400**，用户可在 `agent.max_turns` 覆盖）。若最后一步在单步子轮次内未以无工具回复结束，且全局轮数仍有余量，执行器会先追加一轮不传 tools 的收尾 synthesis；若全局轮数也已用尽或收尾仍异常，则返回**专用说明**。执行阶段 `on_thinking` 的合并 header 为 **`[执行]`**（单循环）或 **`[步骤 i/n]` + 描述摘要**（分步）。
- **Phase 3 反思评估**：`features.reflection` 默认开启；设为 `false` 可关闭。执行完成后，[`run_agent`](miniagent/core/agent.py) 调用 [`reflect_on_result`](miniagent/core/problem_solver.py) 对回复做自我反驳式审查。`on_thinking=None` 确保反思过程不产生额外的思考步骤。
- **思考深度与供应商**：[`resolve_exec_completion_kwargs`](miniagent/core/llm_params.py) / [`resolve_planner_completion_kwargs`](miniagent/core/llm_params.py) 合并 `model.thinking_level` / `model.thinking_budget`；DashScope/Qwen 兼容 `base_url` 时通过 [`build_thinking_extra_body`](miniagent/core/vendor/qwen_extra.py) 注入 `extra_body`。`model.max_tokens` 覆盖输出 token 上限。

## 各层详细说明

### 1. 入口层 (Entry)

| 文件 | 职责 |
|------|------|
| `__main__.py` | 统一入口：加载凭据、`--stop` 子命令，其余委托 `compat.unified_entry` |
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
| `command_dispatch.py` | 统一命令调度器：CLI 和飞书共享 `/` 命令（支持双前缀），输出捕获（StringIO） |
| `cli_commands.py` | CLI 命令实现：/session, /instance, /queue, /help, /copy（全屏）等 |
| `background_tasks.py` | `BackgroundTaskManager`：后台任务生命周期管理、并行上限控制 |
| `btw_cmd.py` | /btw 命令实现：启动、查询、取消后台任务 |
| `doctor.py` | 环境诊断：Python 版本、依赖、配置检查 |
| `model_cmd.py` | /model 命令：查看/切换当前模型 |
| `feishu_state.py` | `FeishuRuntime`：飞书 WebSocket 长连接任务 start/stop/status |
| `session_lock.py` | 会话级锁管理：PID 存活检测、跨实例互斥 |
| `thinking.py` | `ThinkingDisplay`：CLI 实时打印 / 飞书缓冲模式 |
| `init.py` | 子系统初始化：技能加载、SessionManager、默认会话 |
| `welcome.py` | 欢迎界面：版本号、会话信息 |

#### 2a. 后台任务子系统 (Background Tasks)

后台任务子系统支持在主 session 中启动子 session 并行执行，不污染主对话历史。

**架构设计**：

```
主 session (CLI/飞书)
       │
       ├─ 用户输入 "/btw start <提示词>"
       │
       ↓
BackgroundTaskManager
       │
       ├─ 生成唯一 task_id (UUID[:8])
       ├─ 创建独立 session_key: __bg__<task_id>
       │
       ↓
asyncio.create_task(_execute_task)
       │
       ├─ 子 session 独立运行 Agent
       ├─ 不写入主 session history.json
       │
       ↓
TaskStatus 状态追踪
       │
       ├─ pending → running → completed/failed
       │
       ↓
用户查询结果 (/btw result <task_id>)
```

**核心类**：

| 类 | 职责 |
|----|------|
| `BackgroundTaskManager` | 任务生命周期管理、并行上限控制（max=4）、结果缓存 |
| `BackgroundTask` | 任务条目：task_id, session_key, prompt, status, result |
| `TaskStatus` | 状态枚举：pending/running/completed/failed/cancelled |

**Session Isolation**：

- 后台任务使用 `__bg__<uuid>` 作为 session_key
- 完全独立于主 session，不共享历史
- 结果仅在用户主动查询时返回

**并发控制**：

- `asyncio.Lock` 保护 `_running_count`
- 达到并行上限时拒绝新任务
- 任务失败/取消时自动释放计数

**相关命令**：见 [CLI.md](CLI.md) §/btw。

### 2b. 通道路由层 (Router)

| 文件 | 职责 |
|------|------|
| `channel_router.py` | `ChannelRouter`：通道-会话路由器，支持 CLI/飞书私聊绑定到同一主会话，群聊保持独立 |

通道路由层负责将不同输入通道映射到统一的主会话 ID：
- **CLI** (`__cli__`)：可绑定到任意会话，实现 CLI 干预飞书会话
- **飞书私聊** (`feishu_p2p:<sender_id>`)：可绑定到 CLI 会话，实现飞书消息共享
- **飞书群聊** (`feishu:<chat_id>`)：始终独立会话，不参与绑定

**CLI 显示隔离**（与路由并行）：一般模式下群聊 Agent 仍运行但不写入全屏 transcript；群聊聚焦（CLI 绑定 `feishu:oc_*`）时仅镜像当前群。门控在 `miniagent/engine/main.py` 飞书 handler 入站侧实现，思考镜像经 `feishu_mirror_cli` 与引擎对齐。详见 [CHANNEL_BINDING.md](CHANNEL_BINDING.md) §CLI 显示策略。

### 3. 核心层 (Core)

Agent 的大脑，采用 **多阶段架构**（Phase 0.5 需求澄清 → Phase 1 规划 → Phase 2 执行）。

| 文件 | 职责 |
|------|------|
| `agent.py` | 多阶段主入口：`run_agent()` (Clarify→Plan→Execute), `run_pipeline()` (线性管线) |
| `requirement_clarifier.py` | Phase 0.5 需求澄清器：三步法（Wittgenstein→Socrates→Polanyi）将模糊输入转化为结构化需求 |
| `planner.py` | Phase 1 规划器：LLM 分析需求 → 生成 `StructuredPlan` → 选择工具箱 → 估算 tokens |
| `executor.py` | Phase 2 执行器：ReAct 循环；在 `plan.steps` 非空且 Internal 常量 `PHASED_EXECUTION` 开启时按步骤分子循环，每步单独解析 `thinking_level`/`thinking_budget` |
| `config.py` | 配置管理：`MODEL_PROFILES`, `AgentConfig` 合并, 循环检测默认值 |
| `openai_client.py` | 进程内共享 `AsyncOpenAI` 工厂；测试可 `reset_shared_async_openai_for_tests()` |
| `llm_params.py` | 合并规划/执行阶段的 `max_tokens`、thinking 等与供应商相关参数 |
| `thinking_presets.py` | 业务描述深度 → `thinking_level` 等档位映射 |
| `task_classifier.py` | 任务难度预分类（简单任务可跳过结构化规划） |
| `vendor/qwen_extra.py` | 兼容 Qwen/DashScope 时在 `extra_body` 注入 thinking 字段 |
| `self_opt/` | 自我优化子系统（详见 [SELF_OPT.md](SELF_OPT.md)） |
| `prompts/` | 系统提示词模块（详见下方 §提示词模块） |

#### 提示词模块 (prompts/)

基于 Claude 最佳实践，所有系统提示词统一管理在 `miniagent/core/prompts/` 目录下：

| 文件 | 提示词 | 用途 |
|------|--------|------|
| `identity.py` | `AGENT_IDENTITY` | Agent 核心身份、角色定位、行为规范 |
| `planner.py` | `PLAN_SYSTEM_PROMPT` | Phase 1 规划阶段：任务分解、工具箱选择 |
| `classifier.py` | `CLASSIFIER_PROMPT` | Phase 0 任务难度分类（simple/normal/medium/complex） |
| `clarifier.py` | `CLARIFIER_PROMPT` | Phase 0.5 需求澄清（三步法） |
| `reflector.py` | `REFLECTOR_PROMPT` | Phase 3 反思评估：结果质量检测 |
| `reviewer.py` | `REVIEW_PROMPT` | `/review` 命令：答案审查 |
| `improver.py` | `IMPROVE_PROMPT` | `/improve` 命令：答案改进 |
| `feishu_channel.py` | `FEISHU_CHANNEL_HINT_*` | 飞书通道工具说明 |

**结构规范**（遵循 Claude 最佳实践）：

每个提示词使用 XML 标签结构化：

```text
<role>        角色定位：专业身份 + 职责边界
<context>     上下文动机：为什么这样要求，背景说明
<instructions> 明确指令：要做什么，按顺序列出
<examples>    多样化示例：3-5 个不同场景示例
<json_schema> 输出格式：JSON schema 定义
<validation>  自我检查：完成后验证什么
```

**示例**：

```text
<role>
你是 MiniAgent 的任务规划专家。你负责将用户需求分解为结构化执行计划。
</role>

<context>
良好的规划能够：
- 让执行器按步骤完成复杂任务，减少迷失
- 减少不必要的工具调用和迭代次数
</context>

<instructions>
分析用户需求后：
1. 判断任务复杂度（单步/多步/需要工具协作）
2. 将复杂任务分解为独立步骤
...
</instructions>

<examples>
<example index="1" type="simple">
用户输入："读取 config.json 的内容"
输出计划：{...}
</example>
...
</examples>
```

#### Phase 0.5: 需求澄清（三步法）

当任务难度为 **普通或复杂**（非简单）时，`requirement_clarifier.py` 在规划之前执行三步需求澄清：

```
用户输入 "帮我查一下天气"
        ↓
┌─── RequirementClarifier ───┐
│ Step 1 (Wittgenstein)      │ ← 识别模糊表述："查一下"未指定城市
│ Step 2 (Socrates)          │ ← 推断隐含约束：当前时间、用户位置？
│ Step 3 (Polanyi)           │ ← 正反向示例："北京今日天气" vs "天气"
└─────────────┬──────────────┘
              ↓
ClarifiedRequirement（澄清后的需求规格）
  - clarified_goal: "获取指定城市的实时天气信息"
  - boundary_conditions: ["需明确城市名称", "返回温度、天气状况"]
  - output_spec: "简洁文本，含温度和天气状况"
  - examples: ["北京今天天气怎么样"]
  - anti_examples: ["天气"]（过于模糊）
```

**三步方法哲学基础**：
1. **Wittgenstein（语言边界）**：识别模糊表述、未定义概念、歧义词
   > "语言的边界就是世界的边界" — 明确什么是可表达的、什么是不可表达的
2. **Socrates（反向追问）**：推断隐含约束（专业度、格式、时间、范围）
   > 通过反向追问暴露隐式边界条件，直到无法再言语回答为止
3. **Polanyi（示例传递）**：提供正反向示例来传递隐性知识
   > "我们知道的比我们能说出的更多" — 用示例而非规则传递 tacit knowledge

**两种模式**：
- **自动推断**：LLM 一次性分析，零交互，适合非交互场景
- **交互追问**：针对 LLM 识别的模糊点实时向用户追问（需 `ask_user` 回调）

**与记忆层联动**：交互模式下，澄清前会加载会话历史记忆，避免重复追问已回答的问题。

#### ReAct 循环详解（多阶段流程）

```
用户输入
    ↓
┌─ Phase 0: 任务分类 ─────────┐
│  task_classifier.py         │ → 简单 / 普通 / 复杂
└─────────────┬──────────────┘
              ↓
┌─ Phase 0.5: 需求澄清 ───────┐  ← 仅普通/复杂任务
│  requirement_clarifier.py   │ → ClarifiedRequirement
│  （三步法：语言边界→追问→示例）│
└─────────────┬──────────────┘
              ↓
┌─ Phase 1: 规划 ─────────────┐
│  planner.py                 │ → StructuredPlan
│  生成步骤、选工具箱、估 tokens│
└─────────────┬──────────────┘
              ↓
┌─ Phase 2: 执行 ─────────────┐
│  executor.py (ReAct 循环)   │
│  ┌──→ LLM 调用 (Think)      │
│  │       ↓                  │
│  │   有工具调用? ──否──→ 返回 │
│  │       ↓ 是               │
│  │   执行工具 (Act)          │
│  │       ↓                  │
│  │   结果反馈 (Observe)      │
│  │       ↓                  │
│  │   循环检测                │
│  │       ↓                  │
│  └── 继续循环 (max_turns)    │
└─────────────────────────────┘
```

**简单任务优化**：当 `task_classifier.py` 判断任务为「简单」时，跳过 Phase 0.5 和 Phase 1，直接进入 Phase 2 执行（低思考档位），节省 LLM 调用开销。

### 4. 飞书层 (Feishu)

| 文件 | 职责 |
|------|------|
| `poll_server.py` | WebSocket 长连接：WSClient 单例、消息防抖合并、与 `ws_health` 监督衔接（去重已迁移至 `feishu_dedup.py`） |
| `feishu_dedup.py` | 消息去重模块：内存+磁盘双重去重、延迟刷盘、进程退出同步保存 |
| `ws_client.py` | lark WS 客户端包装：暴露 `receive_task` / `connected` 供监督循环使用 |
| `ws_health.py` | 会话监督：看门狗、死连接/空闲刷新、与 `FeishuRuntime` 外层退避重连配合 |
| `main.py` 内 `_create_feishu_handler` | 消息处理器：飞书消息 → Agent → 回复（已从独立 `agent_handler.py` 合并至引擎主循环） |
| `agent_channel_prompts.py` | 通道级提示词配置 |
| `types.py` | `FeishuConfig`, `FeishuEvent` 类型定义 |
| `resource_io.py` | 飞书媒体/资源下载与会话落盘 |
| `im_send.py` | IM 发送客户端封装 |
| `im_tool_policy.py` | 内置飞书工具策略 |
| `lark_response.py` | 飞书响应构建 |
| `docx/` | 云文档：`client`（元数据/raw）、`blocks`（块 CRUD/batch + `append_markdown_to_document`）、`tables`、`media`、`markdown`、`markdown_renderer`（Markdown 富文本渲染） |
| `bitable/` | 多维表格记录与 `upload_record_attachment` |
| `cards/` | 互动卡片构建、入站抽取、按钮路由、可选 v2 宽表 |
| `drive_extra.py` | 云盘搜索（User Token）、权限、copy/move |
| `receive_id.py` | IM 出站 `receive_id` 解析（工具与卡片共用） |
| `drive_client.py` | 云盘列举客户端 |
| `folder_token_resolve.py` | 云盘文件夹 URL / token 解析 |
| `upload_io.py` | 上传并发 I/O |

### 5. 记忆层 (Memory)

三层记忆架构，详见 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)。

| 层 | 文件 | 说明 |
|---|------|------|
| Layer 1 | `store.py` | 短期记忆：会话级记忆存储、事实提取、摘要生成 |
| Layer 2 | `activity_log.py` | 活动日志：详细操作流水，写入 `memory/YYYY-MM-DD.md` |
| Layer 3 | `keyword_index.py` | 语义检索：TF-IDF 加权关键词索引，跨会话搜索 |
| 管理 | `context.py` | 上下文管理：Token 计数、自动压缩、记忆注入、消息窗口 |
| 进程默认 | `defaults.py` | `paths.state_dir`、进程级默认记忆 bundle |
| 管线 | `memory_pipeline.py` | 将记忆/摘要注入对话上下文的管线步骤 |
| 归档 | `history_archive.py` | 历史归档与裁剪策略 |
| 桥接 | `history_bridge.py` | 会话历史与记忆层之间的衔接 |
| 渐进式 | `history_progressive.py` | 渐进式历史披露与按需加载 |
| 分层视图 | `layered_memory.py` | 多层记忆抽象与组装 |
| 周期任务 | `dream_scheduler.py` | 轻量后台精炼 / 长时记忆触发的调度 |

### 6. 会话层 (Session)

| 文件 | 职责 |
|------|------|
| `manager.py` | `SessionManager`：创建/切换/重命名/列出会话，编号↔ID 双重解析，内存+磁盘双查找 |
| `workspace.py` | 工作空间管理：会话目录结构、config.json、history.json |

### 7. 技能层 (Skills)

| 文件 | 职责 |
|------|------|
| `registry.py` | 技能注册表：注册/发现/状态管理 |
| `loader.py` | 技能加载器：动态导入、工具箱提取、Prompt 合并 |
| `paths.py` | 技能根目录解析（`paths.skills_dir` / 默认 `workspaces/skills`） |
| `builtin_toolboxes.py` | 内置工具箱定义，与技能包合并 |
| `clawhub_client.py` | ClawHub 客户端：技能搜索/安装/版本管理 |

### 8. 工具层 (Tools)

LLM 可通过 function calling 调用的工具：

| 文件 | 工具 |
|------|------|
| `exec.py` | 命令执行 (subprocess) |
| `filesystem.py` | 文件操作 (read/write/list/edit) |
| `web.py` | 时间查询 (get_time)、依赖检查 (check_app_availability) |
| `data_tools.py` | 数据处理 (read_csv/write_csv/json_read/json_write) |
| `vision.py` | 视觉理解 (analyze_image)：分析图片内容，生成描述 |
| `skills.py` | 技能操作 (install/uninstall/list) |
| `session_memory.py` | 会话级记忆辅助工具（由 `engine/init` 注册） |
| `knowledge_tools.py` | 知识库检索 (search_knowledge/read_knowledge_file/kb_list)：检索已挂载的本地文档 |
| `cli_dispatch_tools.py` | `run_dot_command`：经 [`command_dispatch.dispatch_command`](miniagent/engine/command_dispatch.py) 执行斜杠命令（`capture=True`，与 CLI 同源） |
| `schedule_tools.py` | `manage_scheduled_task`：定时任务结构化 CRUD |
| `feishu_im_tools.py` | 可选飞书 IM/云文档工具（需 `pip install -e ".[feishu]"`） |
| `feishu_doc_tools.py` | 飞书文档操作（create/append/list_blocks 等） |
| `feishu_bitable_tools.py` | 飞书多维表格操作（get_meta/list_fields/list_records/create_record 等） |
| `feishu_card_tools.py` | 飞书卡片消息更新（update_message_card） |

**run_dot_command 与进程状态**：[`UnifiedEngine.run_agent_with_thinking`](miniagent/engine/engine.py) 将共享 [`CliLoopState`](miniagent/engine/cli_state.py) 写入 `AgentConfig.cli_loop_state`，[`execute_plan`](miniagent/core/executor.py) 再注入 `ToolContext`。飞书入站路径下默认 `cli_dispatch_allow_mutations=False`（与飞书里直接发 `/session` / `/schedule` 变异一致）；**`feishu.dot_commands_full=true`** 时为 True，与 CLI 同等。若嵌入代码只调用 [`run_agent`](miniagent/core/agent.py) 而不经 `run_agent_with_thinking`，需在 `agent_config` 中自行传入 `cli_loop_state`（及按需的 `cli_dispatch_allow_mutations`），否则工具会返回不可用说明。注册开关：**`cli.dot_tools_enabled`**（默认开启，`false` 跳过注册，见 `config.defaults.json`）。

### 8b. MCP（可选）

| 文件 | 职责 |
|------|------|
| `mcp/bridge.py` | MCP 工具定义与 OpenAI function schema 互转 |
| `mcp/runtime.py` | stdio 连接、注册 `mcp_*` 工具；需 `pip install miniagent-python[mcp]` |

`config.user.json` 中 `mcp.stdio_command`（JSON 数组）在 [`engine/init.py`](miniagent/engine/init.py) 启动时解析；详见上文「与 OpenClaw 的关系」中的 MCP 说明。

### 8c. 知识库层 (Knowledge)

挂载本地文档供 Agent 检索，详见 [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)。

| 文件 | 职责 |
|------|------|
| `knowledge/base.py` | `KnowledgeBase`：单知识库加载、关键词索引构建、检索 |
| `knowledge/registry.py` | `KnowledgeRegistry`：多知识库挂载/卸载/跨库检索、持久化 |
| `tools/knowledge_tools.py` | Agent 工具：`search_knowledge`、`read_knowledge_file`、`kb_list` |

**RAG 全面集成**（v2.0.3 新增）：

知识库现已内化到 Agent 的所有核心阶段，实现"主动检索 + 自动注入"双模式：

| 阶段 | RAG 集成方式 | 配置开关 | 检索参数 |
|------|-------------|----------|---------|
| **工具层** | knowledge 工具作为核心工具箱（toolbox=None），始终可用 | `knowledge.as_core=false` 可降级 | - |
| **执行阶段** | 自动检索知识库，注入 system prompt（原有功能） | - | top_k=3, max_chars=4000 |
| **规划阶段** | 检索知识库摘要，辅助判断是否需要 knowledge 工具箱 | `knowledge.planner_enabled` | top_k=2, max_chars=2000 |
| **需求澄清** | 检索知识库内容，避免询问已有答案的问题 | `knowledge.clarifier_enabled` | top_k=3, max_chars=3000 |
| **任务分类** | 检索知识库摘要，辅助判断任务难度（有答案→simple） | `knowledge.classifier_enabled` | top_k=2, max_chars=1500 |
| **反思评估** | 检索知识库标准，参考标准评估回答质量 | `knowledge.reflector_enabled` | top_k=2, max_chars=1500 |

**环境变量**：
- `knowledge.root` / `knowledge.default_root`：知识库根目录（默认 `workspaces/knowledge`）
- `knowledge.auto_mount`：自动挂载根目录下知识库（默认 `true`）
- `knowledge.as_core`：将 knowledge 工具作为核心工具箱（默认 `true`）

详细集成方案见 [RAG_ENHANCEMENT_PLAN.md](RAG_ENHANCEMENT_PLAN.md)。

### 9. 基础设施层 (Infrastructure)

| 文件 | 职责 |
|------|------|
| `registry.py` | `DefaultToolRegistry`：工具注册/查找/Schema 导出（实现 `ToolRegistryProtocol`） |
| `monitor.py` | 性能监控器：耗时统计、成功率追踪 |
| `message_queue.py` | 消息队列：按 chat_id 隔离、queue/preemptive 双模式、耗时追踪 |
| `channel_router.py` | CLI / 飞书私聊 / 群聊 → `session_key` 与绑定关系 |
| `instance.py` | 多实例注册表：自增 ID、心跳（观测）、PID 僵尸目录清理（非心跳超时） |
| `feishu_inbound_lock.py` | 飞书 WebSocket 入站跨进程独占（磁盘锁） |
| `env_loader.py` | 加载 `config.user.json` 的 `secrets` 部分到环境变量 |
| `json_config.py` | JSON配置加载器：分层配置、点路径访问、环境变量覆盖 |
| `env_parse.py` | `env_flag` / `env_flag_strict` 环境变量解析 |
| `timezone_config.py` | `process_timezone()`（`timezone.default` / `TZ`） |
| `tracing.py` | 轻量追踪/跨度钩子（与日志配合） |
| `logger.py` | 日志系统：`append_log()`, `get_logger()` |
| `loop_detector.py` | 循环检测器：相似度检测、warning/critical 分级 |
| `process.py` | 进程管理：子进程追踪、孤儿进程清理 |
| `debug_ndjson.py` | 可选 NDJSON 调试落盘 |
| `http_retry.py` | HTTP 重试工具：指数退避、5xx 重试、网络错误重试 |
| `config_watch.py` | 配置热更新：监听文件 mtime、防抖触发 reload |

### 用户体验增强层

位于 `miniagent/engine/` 和 `miniagent/infrastructure/`，提供交互体验优化：

| 模块 | 功能 |
|------|------|
| `command_dispatch.py` | 模糊匹配：`difflib.get_close_matches()`、前缀匹配、建议提示 |
| `main.py` | Tab 补全：`CommandCompleter`（命令）、`FilePathCompleter`（路径） |
| `setup_wizard.py` | 首次配置引导：交互式 API 密钥、模型、端点配置 |
| `openai_client.py` | 超时配置：SDK 原生 `timeout`、`max_retries` 参数 |

**增强功能**：
- **命令模糊匹配**：错别字检测（如 `/sttatus` → `/status`）、相似度阈值 0.6、建议而非自动执行
- **Tab 自动补全**：命令补全（`/` 开头）、文件路径补全（`@file:` 开头）、Tab/Shift+Tab 循环
- **网络可靠性**：HTTP 重试（3 次、指数退避）、连接池复用、超时配置（120 秒）
- **配置热更新**：文件监听（5 秒检查）、防抖（2 秒）、`/reload-config` 命令

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

**通道绑定影响**：`/session switch` 会更新 `ChannelRouter` 绑定（CLI 与自动跟随的飞书私聊 sender）。
CLI 输入经 `channel_router.resolve("__cli__")` 解析 session_key，使用对应会话的上下文（记忆/文件/工具共享）。

### 飞书数据流

```
飞书平台 → WSClient (WebSocket) → poll_server.on_message_receive()
    → 去重检查 → message_queue.dispatch(chat_id, ...)
    → text: handler() → 命令拦截 (/开头) 或 engine.run_agent_with_thinking()
    → file/image/post: media_handler() → 资源下载 → 会话 files/feishu_incoming/
        → channel_router.resolve_feishu_message(chat_id, sender_id, chat_type)
        → run_agent() → planner → executor (ReAct)
        → 工具调用 → LLM 回复 → 飞书 API 发送回复
```

`FeishuRuntime` 在同进程内对 `start_feishu_poll_server` 做**退避重连**；`feishu_inbound_owner` 锁在重连期间仍由该实例持有。连接成功后由 `miniagent/feishu/ws_health.py` 的**会话监督**（收包 task、看门狗、可选定期刷新）替代裸阻塞；断线或收包循环结束会在数秒内结束当前会话并触发外层重建，详见 `docs/FEISHU.md`「Windows / 长连接」。

**通道绑定影响**：飞书私聊消息通过 `resolve_feishu_message()` 解析：
- 若 `feishu_p2p:<sender_id>` 已绑定，则使用绑定的 session_key
- 群聊消息始终使用独立的 `feishu:<chat_id>` session_key

### 命令调度流（CLI + 飞书共享）

```
用户输入 "/status"
    ↓
command_dispatch.dispatch_command()
    ↓ capture=True (飞书) / False (CLI)
_format_status(state, message_queue)
    ↓
返回字符串 → 飞书回复 / CLI print
```

## 定时任务子系统

与「记忆层 `dream_scheduler`」不同，本节的 **定时任务**指用户配置的 **周期性/一次性 Agent 回合**：任务定义持久化在磁盘，由进程内后台循环触发，经与聊天相同的 **消息队列** 进入 `UnifiedEngine.run_agent_with_thinking`。

### 持久化与路径

- **文件**：`{paths.state_dir}/scheduled_tasks/tasks.json`（默认 `workspaces/scheduled_tasks/tasks.json`）。读写见 [`miniagent/scheduled_tasks/store.py`](../miniagent/scheduled_tasks/store.py)。
- **Git**：该目录为运行时状态，应在 `.gitignore` 中排除（与 [ENGINEERING.md](ENGINEERING.md) §3.1 一致）。

### 运行时链路

1. **启动**：[`unified_main` / CLI 主循环](../miniagent/engine/main.py) 在构造 `RuntimeContext` 后调用 [`start_scheduled_tasks_ticker`](../miniagent/scheduled_tasks/ticker.py)，将 `asyncio.Task` 记入 `RuntimeContext.scheduled_tasks_ticker`，并用 `scheduled_tasks_stop_event` 协作退出。
2. **Ticker**：[`tick_once`](../miniagent/scheduled_tasks/ticker.py) 在取得 `scheduler.lock` 后 `load_tasks()`，经 `repair_invalid_schedules` 补齐/校验 cron；对到期任务先取 `job_<id>.lock` 再投递协程；同进程 `_inflight` 防重入；每 tick 最多 `_MAX_DUE_PER_TICK` 条。
3. **Runner**：[`build_run_scheduled_job_coro`](../miniagent/scheduled_tasks/runner.py) 经 [`resolve_execution_target`](../miniagent/scheduled_tasks/resolve.py) 与 [`resolve_feishu_delivery`](../miniagent/scheduled_tasks/feishu_delivery.py) 解析 `session_key`、消息队列 `chat_id`（与入站 `poll_server.dispatch(chat_id)` 对齐）及飞书 `receive_id`；合成 prompt 后调用 `UnifiedEngine.run_agent_with_thinking`（`is_feishu=True` 时推送思考卡）；最终回复由 runner 调用 `_send_reply`（与入站 handler 对称）。默认时区见 [`timezone_util.py`](../miniagent/scheduled_tasks/timezone_util.py)。
4. **用户入口**：终端与 CLI 侧 **`/schedule`** 子命令（`every` / `once` / **`cron`** 五段表达式，实现见 [`cli_commands.py`](../miniagent/engine/cli_commands.py)）；Agent 可选 **`manage_scheduled_task`**（含 `add_cron`，[`schedule_tools.py`](../miniagent/tools/schedule_tools.py)）；下一触发时间由 [`cron.py`](../miniagent/scheduled_tasks/cron.py) + **croniter** 计算。
5. **并发**：`scheduler.lock`（tick）+ `job_<id>.lock`（执行）+ `tasks.json.lock`（读写）；dispatch 失败时 `next_run_at` 默认退避 60s，可由 **`scheduled_tasks.dispatch_backoff`** 覆盖（见 [`store.py`](../miniagent/scheduled_tasks/store.py) 与 `config.defaults.json`）。

### 配置项（JSON）

| JSON 路径 | 作用 |
|-----------|------|
| `scheduled_tasks.disabled` | 为真时 ticker 早退，不调度定时任务 |
| `scheduled_tasks.dispatch_backoff` | dispatch 失败时推迟 `next_run_at` 的秒数（默认 60） |
| `timezone.default` | 进程默认 IANA 时区（Agent system、`get_time`；优先级高于 `TZ`） |
| `scheduled_tasks.timezone` | 仅定时任务**新建**默认时区；未设则 `timezone.default` / `TZ` |
| `TZ` | 操作系统时区 env（与 JSON 兼容） |
| `scheduled_tasks.feishu_mirror` | `false` 时关闭 primary→已绑定飞书的镜像投递（默认开启） |
| `scheduled_tasks.feishu_last_chat` | `true` 时无绑定时可回退到 `last_feishu_receive_chat_id`（默认关闭） |
| `scheduled_tools.enabled` | `false` 时不注册 `manage_scheduled_task` |
| `cli.dot_tools_enabled` | `false` 时不注册 `run_dot_command` |

**飞书侧**：与 `/session` 类似，通常仅允许 `/schedule list` / `show`；`add` / `remove` / `enable` / `disable` 须在本地 CLI 执行（详见 [README.md](../README.md) 与 [USER_GUIDE.md](USER_GUIDE.md) §8）。

### 数据流示意

```mermaid
flowchart LR
  tasksJson[tasks_json]
  ticker[Ticker_tick_once]
  runner[Runner_build_job]
  engineNode[UnifiedEngine]
  mq[MessageQueue]
  tasksJson --> ticker
  ticker --> runner
  runner --> engineNode
  engineNode --> mq
```

## 关键设计决策

### 为什么选择多阶段架构？

采用 **Phase 0 (分类) → Phase 0.5 (澄清) → Phase 1 (规划) → Phase 2 (执行)** 四阶段流水线：

1. **Phase 0 (任务分类)**: `task_classifier.py` 快速判断任务难度（简单/普通/复杂），简单任务跳过后续规划
2. **Phase 0.5 (需求澄清)**: `requirement_clarifier.py` 三步法（Wittgenstein→Socrates→Polanyi）将模糊输入转化为结构化需求
3. **Phase 1 (规划)**: `planner.py` 分析澄清后的需求，选择合适的工具箱，估算资源消耗，生成步骤化执行计划
4. **Phase 2 (执行)**: `executor.py` 只加载必要的工具，按步骤执行 ReAct 循环

**好处**：
- 简单任务快速响应（跳过 Phase 0.5 和 Phase 1）
- 模糊需求先澄清再规划，减少无效迭代
- 复杂任务精确规划，按步骤执行，支持断点续跑
- 动态调整思考档位，平衡响应质量与成本

### 消息队列设计

两种模式适配不同场景：
- **queue 模式**：按顺序处理，适合需要上下文连贯的场景
- **preemptive 模式**：最新消息优先，适合实时交互场景

按 `chat_id` 隔离队列，防止不同用户/群的消息互相干扰。

### 多实例设计

多实例注册表、PID 存活清理与心跳语义详见 [ENGINEERING.md](ENGINEERING.md) §3.3（SSOT）。要点：新实例 `register()` / `list_all()` 按 OS PID 清理僵尸目录，**不**向其它进程发终止信号；会话级锁保证数据一致性。

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

6. **飞书私聊与 CLI 同会话**：首条私聊自动 `bind` 到 `active_session_id`；`/session switch` 通过 `sync_channel_router_to_session()` 同步 CLI 与已登记的私聊 sender。飞书 WebSocket 入站由 `feishu_inbound_owner.json` 做跨进程独占。

7. **分层记忆管线**：`history_archive` 归档、`memory_pipeline` 注入、`dream_scheduler` 周期/体量触发的轻量精炼与 `session_lt` / `agent_lt` 更新（详见 [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)）。

## 运行时组合根

启动时由 `compat.unified_entry`（或等价入口）实例化 `RuntimeContext`（见 `runtime/context.py`）；`unified_entry` 会先从 `config.user.json` 加载 secrets 到环境变量。仅嵌入 `unified_main(ctx)` 时，调用方须自行 `load_secrets_from_project_root()` 或设置 env。字段包括 `registry`、`monitor`、`skill_registry`、`clawhub`、`engine`，以及 **`channel_router`**、**`message_queue`**、**`feishu`**（`FeishuRuntime`），以及 **`memory_store`**、**`activity_log`**、**`keyword_index`**，以及 **`openai_client`**（入口通常设为 `get_shared_async_openai()`；为 `None` 时执行链回落共享工厂）。`unified_main`、CLI 主循环与飞书消息处理器通过该对象（或由其闭包捕获）获取依赖，避免在 `compat`/`unified` 等模块上维护可变全局。

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
| 添加技能 | `workspaces/skills/<pkg>/` | 包级 `SKILL.md`，工具见 `skills/<name>/SKILL.md` 与 `tools.py`（约定见 `miniagent/skills/loader.py`、`workspaces/skills/skill-creator/SKILL.md`）；`install_skill` / `/reload-skills` / `refresh_skills` 可热加载，无需重启 |

### 技能热加载（`refresh_skills`）

- **入口**：进程启动 `init_subsystems` → `bootstrap_skill_packages`；运行期 `install_skill`（单包）、`/reload-skills` / `features.skills_watch`（全量）。
- **快照**：`state["skill_toolboxes"]` / `state["skill_prompts"]`；`run_agent` 与飞书 handler 每次从 state 读取，refresh 后**下一回合**生效。
- **Gating**：`get_all_toolboxes` / `get_system_prompts` 仅聚合 `get_eligible_skills`；全量 refresh 卸载主 registry 工具时遍历**全部**已注册技能（含被 gating 的），避免幽灵工具。
- **子会话**：refresh 只更新主空间 `registry`；已创建子会话的克隆工具集不会自动同步，需新建会话或 promote。
| 添加命令 | `command_dispatch.py` | 注册新路由 |
| 自定义模型 | `config.user.json` | 支持任何 OpenAI 兼容 API |
| 新通道 | 仿照 `feishu/` | 实现消息接收 + 回复发送 |
