# Changelog

本文件记录面向使用者与维护者的最终发布结果。版本号以
`miniagent.__version__` 为准。

## [Unreleased]

### 架构

- `miniagent.bootstrap.entrypoint` 是唯一启动入口，负责构造
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

- 唯一默认配置位于 `miniagent/resources/config.defaults.json`，并作为 wheel 包资源发布。
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

- 飞书 `/feishu start|stop` 直接激活或停用 `FeishuRuntimeLifecycleService`。
- 关停流程先停止生命周期生产者，再取消并等待后台任务和消息队列消费者，最后关闭记忆、
  Trace writer、LLM 客户端和进程资源；异常启动与 CLI 异常同样由 `run_runtime` 的 `finally` 收口。
- 配置热更新显式接收 `ApplicationContainer`，先严格解析候选 loader 并构造候选 AsyncOpenAI 客户端，成功后整体发布；旧连接池作为 retired 资源保留到统一关停，避免中断在途请求。失败时当前配置与客户端均保持不变。
- 飞书文本、媒体、命令和定时投递共用标准出站路径，避免重复发送。
- CLI 与飞书 thinking 在最终回复前 drain，保证同会话顺序与取消传播。

### 文档与质量

- README、架构、工程、CLI、飞书、记忆和性能文档统一描述当前组合根、生命周期、消息边界、
  配置资源与状态路径。
- CI 执行 Ruff、compileall、Mypy、架构边界、非评测测试、wheel 构建与资源检查。
- 删除失效的架构图、重复默认配置、重复测试和仅服务于已删除接口的辅助类型。

## [2.1.0] - 2026-07-11

- 提供两阶段 Agent 执行、CLI 与飞书通道、会话与记忆、定时任务、后台任务、技能系统、
  MCP、知识库、Trace 与自我优化能力。
- 支持 Python 3.10–3.12，并提供 `feishu`、`browser`、`mcp`、`cli`、`dev` 与 `typing`
  可选依赖组。
