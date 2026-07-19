# Changelog

本文件记录面向使用者与维护者的最终发布结果。版本号以
`miniagent.__version__` 为准。

## [Unreleased]

### Fixed

- AgentRuntime 同一 session 的排队请求不再占用跨 session 并发槽；排队取消会回收会话锁计数，异步事件投递受关停超时约束。
- ComposedAssistantRuntime 串行化并发 start/stop，关停后清理 run/target 索引，并在聚合普通错误时保留组件名称与控制流异常。
- 修复命令白名单与关停任务集合的静态类型错误，以及非有限 embedding 测试在 httpx 响应构造阶段提前失败的问题。
- 文档门禁新增 defaults 配置键与环境变量声明核对；修正组合模式、LLM 重试键、飞书 PATCH 常量、evaluation 配置和第三方技能清单漂移。
- 命令白名单接受 JSON 数组，并将显式空数组解释为“禁用全部命令”；无效类型失败关闭，不再意外恢复默认允许列表。
- 飞书入站兼容 Lark 毫秒时间戳，消息过期过滤与标准入站时间保持一致。
- AgentRuntime 回收空闲会话锁，并以关停超时约束异步事件投递；CLI surface 恢复目标校验，RAG 文档替换不再保留旧向量，Embedding 拒绝 NaN/Infinity。
- 公共四模块 docstring 门禁恢复通过；架构、配置、coverage、可选依赖和安全文档与当前源码同步。

### 4.0 Architecture

- Agent 新增生命周期完整的 `AgentRuntime`、不可变 `AgentEvent`、run 级取消、按会话串行和跨会话并行执行；RAG 与 JSONL Trace 作为实例隔离的 `AgentExtension` 接入。
- LLM 保留原模块与公开命名，并新增统一 `EmbeddingClient`；现有 `llm.*` 配置、Session、Memory、知识库与 Trace 数据格式不因模块重构而改名。
- UI 成为消息、渠道、CLI/TUI/飞书 surface 和飞书标准化适配的所有者；Assistant 通过 `AssistantSpec`、`PersonalAssistantSpec` 与 `create_assistant()` 组合实例。
- 架构门禁更新为 `llm ← agent ← ui ← assistant`，其中 Assistant 可直接装配 Agent/LLM，禁止所有反向依赖。

### Breaking Changes

- 源码包收敛为 `llm`、`agent`、`ui`、`assistant` 四个主模块；旧的 `core`、`engine`、
  `contracts`、`types`、`infrastructure` 等顶层导入路径已移除，不提供转发兼容层。
- LLM 配置只接受 `llm.providers`、`llm.models`、`llm.roles` 与 `secrets.llm`；运行时
  不迁移或写回旧配置和状态，人工升级步骤见 [docs/MIGRATION.md](docs/MIGRATION.md)。
  包版本进入 4.0.0；3.0 状态 schema 保持不变。

### Added

- 新增 Trace 开销基准与有界稳定性浸泡；CI 覆盖 Python 3.10/3.12/3.13、Windows 与 macOS 冒烟，以及 Anthropic、Google、Playwright 等可选依赖。
- Trace 事件增加显式 schema 版本与集中事件注册表，真实 API harness 产出 schema v3 摘要，并将缺失/空 Trace、writer 终态计数不一致和秘密命中视为硬失败。
- 新增协议无关 `LLMGateway`、显式 provider registry、小型模型目录及
  `default/reasoning/fast/vision` 角色路由；支持 OpenAI Chat/Responses，并通过可选
  `providers` extra 支持 Anthropic Messages 与 Google Generate Content。
- 模型动态刷新采用 last-known-good 原子缓存；provider 错误、工具调用、推理、用量和取消
  归一化为共享契约。
- 全屏 TUI 新增多行输入、回答期间继续排队、模型/会话选择器、响应式状态栏、默认折叠推理
  和可校验快捷键配置；保留 fallback CLI。

### Changed

- 移除易漂移的逐文件哈希审计账本，改由文档版本、命令覆盖、仓库卫生、静态分析、覆盖率和真实性能测试直接提供可执行质量证据。
- 命令元数据显式区分共享 dispatcher 与 CLI 前端本地命令；`/copy` 进入 CLI 帮助和补全事实源，`/query` 明确为 `/queue status` 的只读别名。
- 文档统一说明 3.0 状态 schema 严格拒绝旧格式且不自动迁移，并将 Dream 能力准确描述为不调用 LLM 的索引与裁剪维护。

- Agent 对象 API 与兼容 `run_agent()` 函数 API 归一到同一冻结回合上下文；Assistant 生产路径不再把对象调用绕回函数入口。执行阶段的单调用点辅助模块已就近合并，同时保持全部公开签名和阶段行为不变。
- `AssistantTurnService` 以不可变回合快照传递 CLI/飞书元数据；命令调度器只负责解析与调用，状态、质量审查和测试逻辑由各自命令模块拥有，并移除命令到调度器的反向依赖。
- 会话配置扫描、缓存、原子配置写入与历史 schema I/O 收敛到私有 `SessionDiskStorage`；`DefaultSessionManager` 的公开方法、目录布局、config schema 1 和 history schema 2 保持不变。
- `ConfigSnapshot` 复用 `AgentSettings` 的递归冻结语义，已有冻结快照进入 Agent 时不再重复复制。
- Trace 配置改由组合根以不可变 `TraceRuntimeConfig` 显式注入；初始化失败可回滚重试，writer 关停超时保留可重试状态，维护命令在溢出竞争下保持 FIFO。CPU/RSS 资源采样不再隐式启动 tracemalloc，Python 分配跟踪由独立配置按需启用。
- Dream 文件维护、配置 watcher 的文件状态读取和索引持久化移出事件循环；embedding cache/single-flight 按 endpoint 与 model 隔离，运行期配置覆盖不再被快照磁盘重读覆盖。
- 内存剖析脚本复用单一事件循环并覆盖正式 `record_turn` 路径；稳定性报告增加预热/末尾 RSS 与 Python 分配中位平台变化，避免用启动峰值误判长驻泄漏。
- 真实 API evaluation 改由所选 provider gateway 验证凭据，移除 OpenAI 专属环境变量和已退役执行器参数；稳定性浸泡在完整对象图与 Trace 路径预热后再开始资源采样。
- 应用组合根持有 LLM gateway 快照；配置热更新不关闭在途请求使用的旧 gateway。
- 会话历史 schema 升至 v2，并标记跨 provider 的稳定消息格式；旧格式直接拒绝且文件不变。
- 架构检查升级为四模块白名单、完整 AST 导入扫描和跨层循环检测；函数内、相对及
  `TYPE_CHECKING` 导入均不能绕过规则。

### 体验

- 复杂任务长内容分层：计划即时预览封顶、分步中间步收成状态行（TUI 替换 / 飞书强制 PATCH）；完整细节仍写入会话历史。显示与通道配置见 `features.*`、`cli.*`、`feishu.card.*`，说明见 `docs/OUTPUT_FORMAT.md`。
- 最终回复结论先行改由执行 prompt「回复结构」约束，展示层不再硬插 `## 结论`。
- 新增独立 `builtin-stackexchange` 基线技能：软硬件排障时主动检索 Stack Overflow 及对应 Stack Exchange 站点，结构化返回采纳/高票答案、作者、日期、票数和来源链接；匿名模式可用，并包含查询脱敏、缓存、配额与 API backoff 保护。

## [2.2.0] - 2026-07-14

### 文档

- 补齐 `workspaces/skills/THIRD_PARTY_SKILLS.md`（技能来源与许可合规 SSOT）。
- 修正文档中的配置/环境变量漂移：`model.retry_count`、时区解析优先级、
  `MINIAGENT_DISABLE_SCHEDULED_TASKS` 等运维 env 写入 ENGINEERING §1.2。
- 更新 CONTRIBUTING：CLI 命令以 `command_registry.CommandSpec` 为 SSOT；
  通道扩展对齐 `ChannelAdapter` / `InboundMessage`。
- README 专题索引补齐 `OUTPUT_FORMAT.md`；收敛 CLI 与渲染文档交叉叙述；
  FEISHU 文首补充控制台快速开始清单。

### 架构

- 增加不可变配置快照、结构化状态 schema/显式备份迁移、带只读 callable 绑定的统一命令注册表，并将飞书卡片渲染策略从长轮询模块拆出。
- 将自优化命令、CLI 补全、transcript 缓冲、执行 prompt 与流式聚合迁入独立组件；TUI 由 `_TuiApplication` 统一持有布局、输入、输出和关闭生命周期，保留原有导入和用户命令兼容路径。
- 将 Agent 生命周期、规划/分类恢复、ReAct 资源装配、引擎会话收尾和飞书思考状态收敛为小型对象；传输 DTO、能力学习、计划/输出格式化、线性 pipeline、实例渲染和飞书 Docx schema 各自拥有独立模块。

- `miniagent.assistant.bootstrap.entrypoint` 是唯一启动入口，负责构造
  `ApplicationContainer` 并调用 `run_runtime`。
- `ApplicationContainer` 是唯一组合根；工具、技能、会话、消息队列、记忆、飞书、
  LLM 客户端与出站通道均通过显式依赖传递。
- `LifecycleManager` 统一管理配置监听、飞书连接、定时任务与技能监听，按注册顺序启动、
  逆序停止，并聚合生命周期错误。
- `MemoryRuntime` 聚合共享注册表、关键词/嵌入索引、存储、活动日志、上下文服务与 Dream 调度器；入口只构造
  一套对象图，执行边界显式注入，关停时停止维护任务、关闭 embedding 连接池并统一刷盘，不再提供进程默认 bundle 或 atexit 定位器。
- `BackgroundTaskManager` 由 `ApplicationContainer` 显式持有；在关停时停止接收任务、取消
  清理循环和执行任务，并等待子会话清理完成，不再使用模块级单例。
- `contracts` 定义平台无关消息与通道协议；`application.messaging` 提供入站协调、出站注册、
  同会话有序分发和失败聚合。
- CLI、飞书、定时任务与后台任务均在应用边界映射 `InboundMessage` / `OutboundEvent`，
  出站发送统一经过 `ChannelRegistry`。
- CLI 进程编排、prompt_toolkit TUI、行式 fallback、输入历史、文件摄取和 shell 执行拆为独立 owner；
  `engine/main.py` 只保留启动与统一关停。
- `scripts/check_architecture.py` 与对应测试约束 `contracts`、`application`、`types` 的依赖方向。

### 配置与状态

- 严格配置热更新会报告未知键的完整点路径；持久化状态保存时写入 `schema_version`，旧文件读取先校验并备份，再通过集中迁移注册表原子发布；失败保留原文件并返回带路径错误。
- `--doctor` 的依赖诊断与 extras 对齐：WebSocket 归入飞书可选依赖，并补充 browser、MCP 可选组，核心安装不再因缺少飞书依赖被误报为损坏。

- 唯一默认配置位于 `miniagent/assistant/resources/config.defaults.json`，并作为 wheel 包资源发布。
- `config.user.json` 只保存本地覆盖与凭据；首次启动引导可直接生成最小配置。
- 默认配置资源、内置技能资源和 wheel 内容由 CI 门禁校验。
- 项目状态目录采用确定性解析：
  `MINIAGENT_PATHS_STATE_DIR` → 绝对 `paths.state_dir/projects/{project_key}` →
  canonical `workspaces/projects/{project_key}`。
- 实例注册表只读取一个明确根目录；`--state-dir` 可显式选择运维目标。
- 定时任务直接持久化最终 IANA 时区，不保存额外来源标志。

### API 收敛

- `AgentConfig` 只保留职责分组：`SessionBindingConfig` 与 `FeishuChannelConfig`；
  `merge_agent_config` 对分组字段逐项合并。
- 执行 prompt 固定分为稳定 system 前缀与本轮动态 user context，避免动态内容破坏前缀缓存。
- thinking 回调使用一个固定签名：三个位置参数和完整关键字元数据
  `full_record`、`reset`、`is_last_step`。
- 定时执行只保留 `ScheduledJob` / `build_scheduled_job`。
- 默认上下文管理器不再承担记忆注入；结构化记忆统一进入本轮 user context。
- Trace writer 只写入带 PID 的分片文件；统计 API 聚合同一天的全部进程分片。
- 删除 OpenAI 客户端 service locator 与所有核心回退路径；组合根唯一创建客户端。
- 删除知识库全局注册表与挂载/检索便捷 API；容器持有的注册表显式贯通全部知识路径。
- 飞书 WebSocket 健康状态、消息/卡片去重、防抖和确认路由均由每个 `FeishuPollState` 持有；
  trace 清理节流由 ticker 实例持有，工具并发限制由 `UnifiedEngine` 持有并显式注入 executor。

### 生命周期与可靠性

- 修复 Unix 子进程组终止超时被 ``OSError`` 分支提前吞掉、无法升级到 SIGKILL 的问题；执行器并行工具阶段显式传播 ``CancelledError``。
- 收紧工具 handler、路径解析、运行时 Protocol 与可选飞书 SDK 边界类型；全包 Mypy（含无注解函数体）成为 CI 必过门禁。
- 飞书 `/feishu start|stop` 直接激活或停用 `FeishuRuntimeLifecycleService`。
- 关停流程先停止生命周期生产者，再取消并等待后台任务和消息队列消费者，最后关闭记忆、
  Trace writer、LLM 客户端和进程资源；异常启动与 CLI 异常同样由 `run_runtime` 的 `finally` 收口。
- 配置热更新显式接收 `ApplicationContainer`，先严格解析候选 loader 并构造候选 AsyncOpenAI 客户端，成功后整体发布；旧连接池作为 retired 资源保留到统一关停，避免中断在途请求。失败时当前配置与客户端均保持不变。
- 飞书文本、媒体、命令和定时投递共用标准出站路径，避免重复发送。
- CLI 与飞书 thinking 在最终回复前 drain，保证同会话顺序与取消传播。

### 文档与质量

- 文档链接、索引、命令元数据与 docstring 增加自动检查；docstring 门禁覆盖公开 API、复杂顶层私有实现与关键状态机，并忽略简单私有控件/协议样板噪声；Ruff 启用 Bugbear、async、性能与 C901（≤15）规则。
- Wheel 资源门禁同时比较源码与制品的 Python 模块清单，阻止复用旧构建目录时夹带已删除模块。
- 架构检查增加生产函数 `≤100` 行的零豁免 AST 门禁；核心编排、TUI、飞书、执行器、规划器和传输层按生命周期/适配职责拆分。
- 扩充 CLI、持久化、类型边界与降级路径的离线回归测试；CI 综合分支覆盖率门禁提升至 80%，并要求完整工作树修改行覆盖率达到 95%，实时数值不再写死在文档。
- 修复定时任务更新验证失败仍落盘、目录条目 `is_dir()` 的 TOCTOU 异常窗口，以及自优化报告版本硬编码问题。
- 性能审计和测试覆盖人工台账的长期结论已并入权威文档，删除易漂移的过程性文件。

- README、架构、工程、CLI、飞书、记忆和性能文档统一描述当前组合根、生命周期、消息边界、
  配置资源与状态路径。
- CI 执行 Ruff、compileall、Mypy、架构边界、非评测测试、wheel 构建与资源检查。
- 删除失效的架构图、重复默认配置、重复测试和仅服务于已删除接口的辅助类型。

## [2.1.0] - 2026-07-11

- 提供两阶段 Agent 执行、CLI 与飞书通道、会话与记忆、定时任务、后台任务、技能系统、
  MCP、知识库、Trace 与自我优化能力。
- 支持 Python 3.10–3.12，并提供 `feishu`、`browser`、`mcp`、`cli`、`dev` 与 `typing`
  可选依赖组。
