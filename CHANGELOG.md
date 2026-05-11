# Changelog

## [Unreleased]

### Changed

- **CLI / 飞书展示（跟进）**：移除未再使用的 `stream_first_body_chunk`；**思考卡与 CLI transcript 顶格输出**（不再对段落首行、列表行或 User/Assistant/思考区正文统一加空格缩进）；`finalize_feishu_thinking_stream` 分片后对各 chunk 跳过第二次 `_normalize_lark_md`（正文已在 `_prepare_thinking_body_for_card` 中规范化）。
- **执行轮数默认**：`AGENT_MAX_TURNS` 默认 400；`MINIAGENT_STEP_MAX_TURNS` 未设置时默认 48；同一步内思考片段默认以双换行拼接（`MINIAGENT_THINKING_SEGMENT_SEPARATOR` 可覆盖）。
- **飞书**：宽 GFM 表超管道阈值时支持 `MINIAGENT_FEISHU_TABLE_FALLBACK`（`both` / `hint` / `unicode`）；思考卡工具区精简；`lark_md` 规范化（孤星号、U+FFFD、水平线、零宽与 `<br>` 等）；同一步多轮 ReAct 默认 PATCH 同一张思考卡。
- **CLI**：可选 `MINIAGENT_CLI_THINKING_RICH` 对非流式思考块 Rich 渲染；全屏 TUI 下思考 Rich 宽度与 Assistant 回复区一致（`set_cli_markdown_width`）；未安装 Rich 时欢迎界面可提示安装 `.[cli]`（`MINIAGENT_WELCOME_CLI_HINT=0` 关闭）。
- **历史落盘**：`on_tool_finish` 默认仅记录工具名与成败；`MINIAGENT_TOOL_FINISH_VERBOSE=1` 恢复详细块。

### Documentation

- **ARCHITECTURE / USER_GUIDE / MEMORY_SYSTEM / README / CLI / FEISHU / INDEX**：与上述默认值及终端 Markdown、飞书表降级、v2 备忘链接对齐；`.env.example` 补充相关变量说明。
- **FEISHU / `.env.example`**：`MINIAGENT_FEISHU_REPLY_PLAIN` 与思考卡累积正文受 `MINI_AGENT_FEISHU_CARD_BODY_MAX` 截断的行为写清（仍为 interactive `lark_md`；完整内容见 history）。
- **[USER_GUIDE.md](docs/USER_GUIDE.md)**：新增零基础全项目使用指南（安装、`.env`、启动、点命令摘要、飞书/搜索/技能/MCP 可选章节、FAQ、安全清单）；[README.md](README.md) 与 [INDEX.md](docs/INDEX.md) 增加入口。
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)**：在「状态目录与多实例注册」后补充会话落盘与 `MINIAGENT_CONFIG` 风险指引，链至 [SECURITY.md](docs/SECURITY.md)。

### Security

- **文档**：[SECURITY.md](docs/SECURITY.md) 新增「外部 JSON（MINIAGENT_CONFIG）与进程环境」专节，说明 `apiKey` 写入 `os.environ` 的风险与缓解；数据安全原则与检查清单与之对齐。

### Engineering

- **`.gitignore`**：默认忽略 `workspaces/memory/`、`workspaces/keyword-index.json`、`workspaces/perf*.jsonl`、`workspaces/feishu_inbound_owner.json`、`workspaces/feishu/`。
- **文档**：[ENGINEERING.md](docs/ENGINEERING.md) §3.1 / §4、[INDEX.md](docs/INDEX.md)「workspaces 与 Git」段落与上述策略一致；[CONTRIBUTING.md](docs/CONTRIBUTING.md) 子包数量表述与目录表一致。
- **注释**：充实 `core`（`agent` / `planner` / `executor`）、`runtime`（`context` / `external_config`）、`engine`（`engine` / `init`）、`compat`、`feishu` 包等模块级说明；`executor` 模块说明中 `ContextBudgetExceeded` 改为全限定类引用；`keyword_index` 模块说明补充勿提交索引文件。
- **示例目录**：新增 [docs/examples/](docs/examples/)（README + 脱敏 `sample-external-config.fragment.json`），与 INDEX 目录树及「勿将密钥放入 workspaces」表述一致。

## [2.0.2] - 2026-05-10

### Packaging

- **`[mcp]` optional extra**：`pyproject.toml` 增加 `mcp>=1.0.0`，与文档及 `MINIAGENT_MCP_STDIO` 安装说明一致；`.env.example` 补充 `pip install miniagent-python[mcp]`。
- **CI**：新增 `test-mcp-extra` job（Python 3.12，`[dev,mcp]` + MCP SDK import 冒烟）。

### Documentation

- **目录树**：`docs/INDEX.md` 与 `docs/ARCHITECTURE.md` 同步 `mcp/`、`memory` 管线模块、`tools/git_readonly`、`session_memory`、`infrastructure/tracing` 等；README「项目结构」改为指向 INDEX。
- **版本标语**：核心与专题文档页眉与 `miniagent.__version__`（**2.0.2**）对齐；完整清单见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §5。补全 `CLI.md`、`FEISHU.md`、`SELF_OPT.md`、`CHANNEL_BINDING.md`、`CYBERNETICS_PLAN.md` 页眉。
- **关键词索引**：`keyword_index` 模块说明标明索引路径相对 ``state_dir`` / ``MINI_AGENT_STATE``。

### Fixes

- **飞书思考**：无 `_output_sink` 时仍维护流式状态，使同轮工具默认合并到单张交互卡片；工具追加后 PATCH 失败时打 `warning`；`_send_reply` 对 `receive_id` 校验支持群聊 `oc_` 与单聊 `ou_`，并统一经 `_normalize_im_receive_chat_id`。
- **欢迎界面版本**：`engine/welcome.get_version()` 改为返回 `miniagent.__version__`，避免 `pyproject.toml` 使用 `dynamic.version` 时误读为 `0.1.0`。

### Engineering

- **注释与模块说明**：充实 `types`、`infrastructure`、`memory`、`session`、`skills`、`tools`、`mcp`、`core`、`engine`、`feishu`、`cli`、`__main__` 等包与关键模块的 docstring（职责边界与导入约定）。
- **测试**：新增 `tests/test_welcome_version.py`，断言 `get_version()` 与 `miniagent.__version__` 一致。
- **`.gitignore`**：增加 `**/__pycache__/`（嵌套字节码目录）。

## [2.0.1] - 2026-05-10

### Documentation

- **文档对齐**：`docs/INDEX.md`、`docs/DEPLOYMENT.md`、`docs/SECURITY.md` 等与 **`miniagent.__version__`（本版 2.0.1）** 一致；移除对已删除的 `unified.py` / `requirements.txt` 的过时描述。
- **新增**：[docs/INSTANCE_REGISTRY.md](docs/INSTANCE_REGISTRY.md)（多实例注册表、PID 判定、`MINI_AGENT_STATE`）。
- **新增**：[docs/ENGINEERING.md](docs/ENGINEERING.md)（质量门禁、单一事实来源、文档维护清单）。
- **索引**：`docs/INDEX.md` 目录树补充 `core/openai_client.py`、`memory/defaults.py` 与 `feishu_state` / `feishu_runtime` 关系说明。
- **勘误**：`DEPLOYMENT.md` 中 Python 最低版本改为与 `pyproject.toml` 一致的 **3.10+**；`FEISHU.md` 更新运行时路径说明。

### Engineering

- **`.gitignore`**：忽略根级别 `debug-*.log`。
- **注释**：充实 `miniagent/__init__.py`、`compat.unified_entry`、`infrastructure/instance` 模块 docstring（注册清理语义与组合根职责）；校正 `core/agent` 模块说明；若干包 `__init__` 与入口注释与架构对齐。
- **README**：补充 `MINI_AGENT_STATE`、实例注册清理说明、开发与静态检查命令；项目结构树补充 `cli/`、`runtime/`、`openai_client`、`defaults`；文档表增加 `ENGINEERING.md`。
- **CI**：`.github/workflows/ci.yml` 增加 `python -m compileall -q miniagent`。
- **配置**：`pyproject.toml` 中 Ruff 增加 `src = ["miniagent", "tests"]`。
- **环境模板**：`.env.example` 增加 `MINI_AGENT_STATE` 说明与示例（默认注释掉）。
- **包结构**：为 `miniagent/feishu/` 补充 `__init__.py`（模块说明，与常规 Python 包布局一致）。

### 第二轮工程化（同 2.0.1 文档修订）

- **workspaces 政策**：[docs/ENGINEERING.md](docs/ENGINEERING.md) §3.1 与 [docs/INDEX.md](docs/INDEX.md) 说明当前跟踪的示例文件与 `.gitignore` 边界；推荐 `MINI_AGENT_STATE`。
- **文档交叉引用**：[docs/SECURITY.md](docs/SECURITY.md) §8；[docs/CLI.md](docs/CLI.md)、[docs/FEISHU.md](docs/FEISHU.md) 文末「相关文档」；README / CONTRIBUTING 中 `git clone <repo-url>` 占位说明。
- **注释**：`engine/main.py`、`core/executor.py`、`core/planner.py`、`engine/command_dispatch.py`、`infrastructure/channel_router.py`、`feishu/poll_server.py`、`infrastructure/instance.py` 等关键分支补充「为何如此」说明；校正 executor/planner 模块标题中的阶段表述。
- **CI**：[`.github/workflows/ci.yml`](.github/workflows/ci.yml) 新增 `test-feishu-extra` job（Python 3.12，`[dev,feishu]`）；[docs/ENGINEERING.md](docs/ENGINEERING.md) §2 同步描述。

### 修复

- **飞书发送思考**：`run_agent_with_thinking` 误将内部 `session_key`（`feishu:oc_...`）当作 IM `receive_id`，导致错误码 230001 `invalid receive_id`。现传入事件中的原始 `chat_id`，并在 `_send_thinking` 中剥离 `feishu:` 前缀以防回归。
- **飞书思考流式**：ReAct 每轮 LLM 流式输出改为 **同一条交互卡片**（首次 `create` + 节流 `patch`），避免每几个 token 新建一条消息；工具意图等仍单独发短卡片。无工具收尾时由 `finalize_feishu_thinking_stream` 补全文。

## [2.0.0] - 2026-05-10

### Breaking changes

- **顶层 `src` 兼容包**：已删除；请使用 `python -m miniagent` 与包 `miniagent`（`pyproject.toml` 不再包含 `src*`）。
- **`miniagent.unified`**：模块已删除；请 `from miniagent.compat import ...`（聚合入口仍以 `compat` 为准）。
- **记忆惰性别名**：移除 `miniagent.memory` 包上的 `memory_store` / `activity_log` 惰性属性；移除 `miniagent.memory.store` / `activity_log` / `keyword_index` 上同名惰性与 `_default_index` 导出。请使用 `get_process_default_memory_bundle()`、`resolve_memory_dependencies()` 或 `RuntimeContext` 注入。
- **`miniagent.types` 飞书类型**：不再导出 `FeishuMessagePayload`、`AgentMessageResult`（内部未使用）；请使用 `miniagent.feishu.types` 中的 `FeishuConfig`、`FeishuMessageEvent`、`FeishuReply` 等。

### Other

- **自我优化 `self_inspect`**：默认扫描目录改为优先 `ctx.cwd/miniagent`，否则回落到 `ctx.cwd`；参数支持 `packageRoot` / `codeDir`，`srcDir` 仍作别名。

## [1.3.0] - 2026-05-10

### 注入与类型

- **记忆默认 bundle**：新增 `miniagent.memory.defaults`（`get_process_default_memory_bundle`、`resolve_memory_dependencies`）。`unified_entry` 与 `execute_plan` / `UnifiedEngine` 在未注入记忆依赖时共用同一套惰性实例，根目录由 `MINI_AGENT_STATE` 决定，消除与旧「import 即构造」模块全局不一致的问题。`miniagent.memory.memory_store` / `activity_log`、子模块内同名导出及 `keyword_index._default_index` 改为惰性并发出 `DeprecationWarning`。
- **LLM 客户端**：`miniagent.core.openai_client.get_shared_async_openai()` 为进程内共享实例；`generate_plan` 与 `execute_plan` 支持可选关键字参数 `client=` 以便测试注入 stub。**RuntimeContext** 增加可选字段 `openai_client`（`unified_entry` 设为共享实例）；`UnifiedEngine.run_agent_with_thinking` 与 CLI / 飞书主路径传入 `client=`，与记忆/clawhub 一样走组合根。
- **RuntimeContext** 增加 `memory_store`、`activity_log`、`keyword_index`；`unified_entry` 按 `MINI_AGENT_STATE` 下的 `workspaces` 根目录构造；`DefaultMemoryStore` 可绑定与之一致的 `KeywordIndex`（写入记忆时更新索引）。
- **ClawHub**：`ToolContext.clawhub`；`SessionManager` 与 `execute_plan` / `run_agent` / `UnifiedEngine.run_agent_with_thinking` 链路注入；`tools/skills` 优先使用上下文客户端。
- **CLI 状态**：新增 `CliLoopState`（`engine/cli_state.py`），`unified_main` 主循环状态与 `dispatch_command` 对齐类型。
- **文档**：`MEMORY_SYSTEM.md`、`CHANGELOG` 已同步。

## [1.2.0] - 2026-05-10

### 运行时与可测试性

- **RuntimeContext** 持有每进程实例：`channel_router`、`message_queue`、`feishu`（`FeishuRuntime`）；入口 `unified_entry` 负责构造。命令调度与 CLI 从 `state["runtime_ctx"]` 或闭包使用上述依赖，不再导出 `channel_router` / `message_queue` 模块级单例。
- **Feishu**：`miniagent.engine.feishu_state.FeishuRuntime` 管理飞书轮询任务；`feishu_runtime` 模块仅作兼容重导出。
- **兼容启动**：曾提供顶层包 `src`（`python -m src` 转发至 `miniagent`）；后续已在仓库中移除，请以 `python -m miniagent` 为准。
- **仓库卫生**：`.gitignore` 扩展忽略常见 `workspaces/` 运行时产物；可选 GitHub Actions 工作流运行 Ruff 与 pytest。

### 其他

- Ruff：清理未使用导入、`E402` 导入顺序、`E741` 含混变量名等，使 `miniagent/` 与 `tests/` 通过 `ruff check`。

## [1.1.0] - 2026-05-10

### Breaking changes

- **包名**：顶层 Python 包由 `src` 更名为 `miniagent`；启动与文档统一为 `python -m miniagent`。
- **打包入口**：`[project.scripts]` 的 `miniagent` 命令指向 `miniagent.cli.cli:main`；`setuptools` 包发现为 `miniagent*`。
- **运行时状态**：移除 `unified` 模块上的可变全局（如 `registry`、`session_manager`、`get_runtime_state` / `set_runtime_state`）；请使用 `RuntimeContext`（`miniagent.runtime.context`）与 `miniagent.compat.unified_entry`。
- **API**：`UnifiedEngine.inject_message` 须传入关键字参数 `session_manager`。

### 其他

- `pyproject.toml` 使用 `setuptools.build_meta` 与动态版本号（`tool.setuptools.dynamic.version` → `miniagent.__version__`）。
- 飞书侧命令调度显式传入 `registry` / `monitor`（不再依赖 `engine.registry` 占位）。
- 文档、`architecture.drawio` 与 CLI 帮助中的路径和命令已同步更新。

## [1.0.0] - 2026-05-09

### 架构重构
- **模块化拆分**: `unified.py` (890行) → `miniagent/engine/` 包 (9个模块)
- **目录重组**: `miniagent/core/` 拆分为 `core/`, `memory/`, `infrastructure/` 三个子包
- **类型系统**: 创建 `miniagent/types/` 包，7个类型定义模块
- **薄兼容层**: `unified.py` 保留为 re-export 层

### 新功能
- **`.status` 命令**: 检查 Agent 状态（不中断执行），CLI 和飞书均可用
- **飞书命令支持**: 飞书消息以 `.` 开头时路由到命令调度器
- **统一命令调度**: `command_dispatch.py` 实现 CLI/飞书共享命令
- **消息队列**: `MessageQueueManager` 支持 queue/preemptive 双模式
- **多实例注册表**: 从单 PID 锁升级为 `InstanceRegistry`（自增ID/心跳/清理）
- **三层记忆**: 短期记忆 + 活动日志 + 语义检索
- **自我优化子系统**: 代码检查、优化提案、Git 快照
- **循环检测**: `LoopDetector` 防止 Agent 无限循环
- **会话管理**: 编号↔ID 双重解析，内存+磁盘双查找

### 飞书集成
- WebSocket 长轮询（无需公网 IP）
- 内存+磁盘双重去重
- 消息防抖合并
- 思考过程缓冲显示

### 安全
- 沙箱环境: 路径白名单 + 父目录遍历拦截
- 会话锁: PID 存活检测 + 跨实例互斥
- 进程管理: 子进程追踪 + 孤儿清理

### 修复
- 对齐「仅 CLI / CLI+飞书」启动形态：实例 `mode` 与飞书启停同步、修正对同步 `feishu_start` 的错误 `await`、文档与注释表述
- 修复 `session_manager` 重启后磁盘会话解析
- 修复 `resolve_session_id` 编号查找
- 修复 `rename_session` 内存找不到时磁盘恢复
- 修复 `cli_commands.py` 编码损坏问题

### 文档
- 新增: INDEX.md, ARCHITECTURE.md, CLI.md, FEISHU.md
- 新增: MEMORY_SYSTEM.md, DEPLOYMENT.md, SECURITY.md, CONTRIBUTING.md
- 新增: architecture.drawio 架构图
- 更新: README.md, CHANGELOG.md, SELF_OPT.md

### 测试
- 78 个单元测试通过, 1 个跳过
- 覆盖: registry, monitor, sandbox, session, skills, loop_detector, instance, memory_store, feishu_types, self_opt_types, integration

### 清理
- 删除 `workspaces/instance.pid`（旧锁文件）
- 删除 `_audit.py`, `scripts/audit_docs.py`（临时脚本）
