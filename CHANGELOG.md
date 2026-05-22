# Changelog

## [Unreleased]

### Added

- **飞书收尾**：`miniagent/feishu/cards/`（构建/入站抽取/按钮路由/可选 CARD_V2 宽表）；工具 `feishu_send_interactive_card`、`feishu_update_message_card`；`feishu_doc` 扩展表格/媒体/import_raw/copy/move/权限/search/`write mode=replace`；`feishu_bitable` + `upload_attachment`；`MINIAGENT_FEISHU_USER_ACCESS_TOKEN`。
- **飞书收尾加固**：`cards/gfm_table` 共用宽表解析；`search` 结构化错误；`upload_drive_media` 公开 API；`write(replace)` 报告删除失败数；工具 schema / 通道提示同步。
- **技能热加载**：`refresh_skills` / `.reload-skills`；`install_skill` 安装后自动加载；`MINIAGENT_SKILLS_WATCH` 可选监视技能目录；CLI/飞书/定时任务从 `state` 读取技能快照。
- **飞书点命令**：环境变量 **`MINIAGENT_FEISHU_DOT_COMMANDS_FULL`**（默认关）使飞书点命令与 CLI 全量对齐（含 `.session`/`.schedule` 变异与 `.stop`）；`dispatch_command` 的 `block_remote` 与 env 一致（防御性）。
- **定时任务**：标准 **5 段 Unix cron**（`croniter`）；CLI `.schedule add … cron "分 时 日 月 周"` 与工具 `add_cron` / **`update`**；`list` 显示本地化下次触发时间。
- **定时任务可靠性**：跨进程 `job_<id>.lock`、`tasks.json.lock`；dispatch 失败退避（`MINIAGENT_SCHEDULE_DISPATCH_BACKOFF`，默认 60s）；非法 cron 写入 `last_error`；shutdown 取消 job 不再误退避。
- **定时任务飞书与时区**：`primary` + 通道绑定时镜像推送最终回复到飞书（`MINIAGENT_SCHEDULE_FEISHU_MIRROR`）；新建任务默认时区链为 `MINIAGENT_SCHEDULE_TIMEZONE` → `MINIAGENT_TIMEZONE` → `TZ` → `Asia/Shanghai`（与 `.env` 一致）。

### Breaking changes

- **飞书内置工具名**：已移除 `feishu_create_document`、`feishu_get_document_markdown`、`feishu_append_document_text`。请改用聚合工具 **`feishu_doc`**（`action=create|read|append|…`）与 **`feishu_bitable`**（`action=get_meta|list_records|…`）。迁移示例：`feishu_create_document` → `feishu_doc` + `action=create`；`feishu_get_document_markdown` → `feishu_doc` + `action=read`。
- **外部 JSON 配置**：已移除 `MINIAGENT_CONFIG`、`MINIAGENT_OPENCLAW_CONFIG` 及 `miniagent/runtime/external_config.py`。请改用 `.env` 扁平变量（`OPENAI_*`、`AGENT_CONTEXT_WINDOW`、`AGENT_THINKING_DEFAULT`、`OPENAI_THINKING_BUDGET`、`OPENAI_MAX_TOKENS` 等）；OpenClaw 字段映射见 [.env.example](.env.example) §2。
- **飞书出站**：未设置 `MINIAGENT_FEISHU_REPLY_TARGET` 时默认 **`reply`**（原为 `create`）；显式 `create` 可恢复旧行为。
- **飞书体验**：`MINIAGENT_FEISHU_REPLY_PLAIN` 默认 **关**（设为 `1` 才开启纯文本模式）；`MINIAGENT_FEISHU_CARD_ACTION_ROUTER` 默认 **开**；无法识别的非空取值视为 **关**（`env_flag_strict`）。
- **飞书工具**：`MINIAGENT_FEISHU_TOOLS_AUTO` 默认 **开**（仍需 `FEISHU_APP_ID`/`SECRET`）；显式 `MINIAGENT_FEISHU_TOOLS=0` 或 `MINIAGENT_FEISHU_TOOLS_AUTO=0` 可关闭。
- **云盘回退**：`FEISHU_DOC_FOLDER_FALLBACK_ROOT_META` 默认 **开**；`0`/`false` 可关闭根目录元数据 API 回退。
- **环境变量别名**：请改用 `MINIAGENT_FEISHU_DOCX_URL_PREFIX`、`MINIAGENT_FEISHU_DOC_FOLDER_TOKEN`；旧名 `FEISHU_DOCX_URL_PREFIX`、`FEISHU_DEFAULT_DOC_FOLDER_TOKEN` 仍会读取并打弃用警告（下一版本可能移除）。

### Changed

- **环境变量**：重组 [.env.example](.env.example)（分节索引、飞书凭证块、推荐配置与进阶分离）；新增 [`miniagent/infrastructure/env_parse.py`](miniagent/infrastructure/env_parse.py)（`env_flag` / `env_flag_strict` / `env_str_legacy`）。
- **定时任务飞书完善**：私聊镜像的消息队列键与入站 `chat_id` 对齐；`MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT` 控制无绑定时的最后聊天回退（默认关）；`repair` 对仍为 UTC 的旧任务打一次性时区提示日志。
- **全局时区 SSOT**：`process_timezone()`（`MINIAGENT_TIMEZONE` / `TZ`）；遗留 UTC 任务 `effective_task_timezone` 按 env 计算、`.schedule align-tz` 写盘；Agent system 与定时任务 prompt 注入本地时间。
- **时区 env 边界**：`process_timezone` 不再读取 `MINIAGENT_SCHEDULE_TIMEZONE`；调度专用变量仅影响 `default_schedule_timezone` / `align-tz`。

- **飞书云文档 / 多维表格**：新增聚合工具 `feishu_doc`、`feishu_bitable`；`miniagent/feishu/docx/` 块级 API 与 `batch_update`；`feishu_doc` + `feishu_list_drive_files` 共用父目录解析：支持在 `folder_token` 中传入**云盘文件夹分享 URL**；列举工具 `folder_token` 可与创建一样省略（回退 `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` 或默认开启的根目录元数据 API）。详见 [FEISHU.md](docs/FEISHU.md)、[.env.example](.env.example)。
- **飞书（复查）**：`run_agent_with_thinking` 合并飞书通道 system 提示时，按与执行器相同的 **effective_registry**（`session_registry` 优先）判断是否已注册 `feishu_*`；`MINIAGENT_FEISHU_TOOLS` 为非认可取值时不再落入 AUTO；[FEISHU.md](docs/FEISHU.md) / [.env.example](.env.example) 写明 `MINIAGENT_FEISHU_TOOLS_AUTO` 在进程 init 即注册、不等待 WebSocket。
- **飞书**：`MINIAGENT_FEISHU_RECEIVE_ID_TYPE` / 入站注入的 `feishu_im_receive_id`（发送者 open_id）与内置工具 `feishu_send_workspace_file` 默认 `receive_id` 对齐；`poll_server` 在 interactive/text 发送失败时记录开放平台错误摘要；[FEISHU.md](docs/FEISHU.md) 标明 docx 为 `block_children.create` 而非 `batch_update`；[README](README.md) / [USER_GUIDE](docs/USER_GUIDE.md) 增加飞书工具索引。
- **CLI 思考**：`ThinkingDisplay` 在 `merge_tools` 后保留流式步骤与已打印长度，与同 `thinking_header` 的飞书单卡对齐；流式阶段 `header` 变更时**无飞书**也会重置流式状态（原逻辑仅在启用飞书时清空）。
- **CLI / 飞书展示（跟进）**：移除未再使用的 `stream_first_body_chunk`；**思考卡与 CLI transcript 顶格输出**；`finalize_feishu_thinking_stream` 分片后对各 chunk 跳过第二次 `_normalize_lark_md`。
- **执行轮数默认**：`AGENT_MAX_TURNS` 默认 400；`MINIAGENT_STEP_MAX_TURNS` 未设置时默认 48；同一步内思考片段默认以双换行拼接（`MINIAGENT_THINKING_SEGMENT_SEPARATOR` 可覆盖）。
- **飞书**：宽 GFM 表超管道阈值时支持 `MINIAGENT_FEISHU_TABLE_FALLBACK`（`both` / `hint` / `unicode`）；思考卡工具区精简；`lark_md` 规范化；同一步多轮 ReAct 默认 PATCH 同一张思考卡。
- **CLI**：可选 `MINIAGENT_CLI_THINKING_RICH` 对非流式思考块 Rich 渲染；全屏 TUI 下思考 Rich 宽度与 Assistant 回复区一致；`MINIAGENT_WELCOME_CLI_HINT=0` 可关闭 Rich 安装提示。
- **历史落盘**：`on_tool_finish` 默认仅记录工具名与成败；`MINIAGENT_TOOL_FINISH_VERBOSE=1` 恢复详细块。

### Performance

- **关键词索引**：`KeywordIndex` 引入 `_dirty`，仅在变更后写盘；`DefaultMemoryStore.add_entry` 不再每次 `save()`，由 `flush_keyword_index()` 与 `executor` 会话记忆保存路径、进程 `atexit` 触发落盘，减少重复整文件重写；批量多次 `add_entry` 后单次 flush 可合并写盘。
- **上下文**：`DefaultContextManager` 对工具 schema 的 token 估算结果做缓存（`set_tools` 时失效），减少 `needs_compression` / `get_token_report` 热路径上重复的 `json.dumps`。
- **文档 / 剖析与合成 perf**：[docs/PERFORMANCE.md](docs/PERFORMANCE.md)、`tests/perf_helpers.py`、`tests/test_perf_synthetic.py`、`tests/perf_baselines/example.json`、`scripts/perf_profile_tracemalloc.py`（`--inner-repeat`）、`scripts/compare_perf_snapshots.py`（根对象校验、`inner_repeat` 不一致 WARN）；`pyproject.toml` 的 `perf` marker；L1 含 S4–S6 及 S7 backlog 说明；`.gitignore` 忽略 `perf-snapshot.json` 与 `perf.out`；Perf smoke 上传带 `${{ github.sha }}` 的 artifact，剖析步骤使用 `--inner-repeat 4`。

### Documentation

- **文档 SSOT 重组**：[INDEX.md](docs/INDEX.md) 目录树补全 `ws_client` / `ws_health` / `env_parse`；统一 `history.json`；README / USER_GUIDE 去重；DEPLOYMENT / EVALUATION_LOCAL 开发安装与 CI 对齐（`.[dev,typing]`）；FEISHU 运维速查与会话隔离说明；ENGINEERING §5 维护清单增强。详情见各专题文档，不在此逐文件列举。
- **历史文档修订**（此前多轮）：多实例 PID 存活语义、FEISHU v2 调研并入 [FEISHU.md](docs/FEISHU.md)、`architecture.drawio` 与 ARCHITECTURE 对齐、USER_GUIDE 零基础指南等。
- **env-only 文档同步**：移除 `runtime/external_config.py` 与 `docs/examples/sample-external-config.fragment.json`；ARCHITECTURE / USER_GUIDE / `.env.example` 改为扁平 env SSOT 与 OpenClaw 迁移表；`architecture.drawio` 去掉 `external_config` 节点；[docs/examples/](docs/examples/) README 改为 env 配置说明。

### Security

- **文档**：[SECURITY.md](docs/SECURITY.md) 删除「外部 JSON（MINIAGENT_CONFIG）与进程环境」专节；数据安全原则与检查清单改为以 `.env` 与进程环境为准。

### Engineering

- **Ruff / 类型 / 覆盖率**：`pyproject.toml` 启用 `[tool.ruff.lint]`（`E4`、`E7`、`E9`、`F`、`I`、`UP`；`E402` 忽略）；`dev` 依赖增加 `pytest-cov`；可选 extra `typing`（`mypy`）与 `[tool.mypy]` 试点配置；`miniagent.types.tool.ToolRegistryProtocol` 内与方法名 ``list`` 冲突的 ``list[...]`` 标注改为 ``List[...]`` 以通过 `mypy miniagent/types`；CI `test` job 安装 `.[dev,typing]` 并跑 `mypy miniagent/types`。
- **`.gitignore`**：默认忽略 `workspaces/memory/`、`workspaces/keyword-index.json`、`workspaces/perf*.jsonl`、`workspaces/feishu_inbound_owner.json`、`workspaces/feishu/`。
- **文档**：[ENGINEERING.md](docs/ENGINEERING.md) §3.1 / §4、[INDEX.md](docs/INDEX.md)「workspaces 与 Git」段落与上述策略一致；[CONTRIBUTING.md](docs/CONTRIBUTING.md) 子包数量表述与目录表一致。
- **注释**：充实 `core`（`agent` / `planner` / `executor`）、`runtime`（`context`）、`engine`（`engine` / `init`）、`compat`、`feishu` 包等模块级说明；`executor` 模块说明中 `ContextBudgetExceeded` 改为全限定类引用；`keyword_index` 模块说明补充勿提交索引文件。
- **注释（补全）**：按清单为 `miniagent/` 内此前缺 docstring 的函数、嵌套函数、Protocol 成员与关键方法补充中文说明（含 `engine/main` TUI 辅助、`feishu/poll_server` 规范化、`memory` 子系统、`tools`、`skills`、`scheduled_tasks`、`mcp` 等）。
- **遗留 env 提示**：[`env_loader.py`](miniagent/infrastructure/env_loader.py) 在加载 `.env` 后若仍设置 `MINIAGENT_CONFIG` / `MINIAGENT_OPENCLAW_CONFIG` 会打一次性 WARNING，指向 `.env.example` §2 迁移说明。

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
- 单元测试通过（用例数以 `pytest tests/ --collect-only -q` 为准）
- 覆盖: registry, monitor, sandbox, session, skills, loop_detector, instance, memory_store, feishu_types, self_opt_types, integration

### 清理
- 删除 `workspaces/instance.pid`（旧锁文件）
- 删除 `_audit.py`, `scripts/audit_docs.py`（临时脚本）
