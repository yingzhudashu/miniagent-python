# 软件工程实践与仓库卫生

> Mini Agent Python | 版本: 3.0.0 | 最后更新: 2026-07-15 | 与 `miniagent.__version__` 对齐

本文档汇总本仓库在**可维护性、可重复构建、安全与协作**上的约定，作为 [CONTRIBUTING.md](CONTRIBUTING.md) 的补充：后者偏「如何写代码」，本文偏「仓库与发布如何保持健康」。

---

## 1. 单一事实来源（Single Source of Truth）

| 主题 | 权威位置 | 说明 |
|------|----------|------|
| 可安装包名与源码布局 | `pyproject.toml` → `[tool.setuptools.packages.find]` | 仅打包 `miniagent*`；不再维护顶层 `src` 作为可导入包。 |
| 版本号 | `miniagent/__init__.py` 中 `__version__` | `pyproject.toml` 通过 `dynamic.version` 读取；发版时与 `CHANGELOG.md`、本文档顶部标语一并更新。**包版本**（如 `2.2.0`）与 `config.defaults.json` 顶层 `version`（**defaults schema version**，当前可为 `2.0.0`）是两条轨道，勿混为一谈。 |
| 依赖声明 | `pyproject.toml` `[project]` / `optional-dependencies` | 不使用根目录 `requirements.txt`；运行时依赖与可选组（`dev`（含 `pytest-cov`）、`feishu`、`browser`、`mcp`、`cli`、`typing`（全包 `mypy`））集中在此。 |
| 配置说明 | [`miniagent/resources/config.defaults.json`](../miniagent/resources/config.defaults.json) + `config.user.json` | 包资源提供默认值，user 文件只写本地覆盖；`_config_guide` 标明 User/Advanced 分层；**勿提交含真实密钥的 user 文件**（见 `.gitignore`）。 |
| 用户安装与首次配置 | [README.md](../README.md) §安装、§配置、§快速入门 | USER_GUIDE / DEPLOYMENT 仅保留专题指针，不重复安装长文。 |
| 通道绑定（CLI↔飞书） | [FEISHU.md](FEISHU.md) §通道绑定 | ARCHITECTURE §2b、USER_GUIDE、CLI 仅保留摘要 + 链接。 |
| 多实例注册表 | 本文 §3.3 | DEPLOYMENT / USER_GUIDE / CLI 只写 `--stop` 用法与 PID 语义摘要。 |
| 定时任务配置 | `miniagent/resources/config.defaults.json` + [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」 | 用户面向摘要见 [USER_GUIDE.md](USER_GUIDE.md) §3；命令语法见 [CLI.md](CLI.md) §/schedule |
| 自我优化（操作） | [SELF_OPT.md](SELF_OPT.md) | `self_optimization` 配置节；提案与 `/self-opt` 命令。 |
| Trace 系统（实现） | 本文 §5 | 事件 schema、writer、stats API；SELF_OPT 只链到此处。 |
| 输出格式与渲染 | [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md) | CLI TUI / 流式 / 飞书卡片间距；CLI.md 侧重命令交互。 |
| 提示词编写 | [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) | ARCHITECTURE §提示词模块只列文件表 + 链接。 |
| 环境变量分类 | 本文 §1.2 | 运维/调试类 env；用户面向配置以 JSON 为准。 |
| 知识库 / RAG | [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) | 挂载、检索、各阶段 RAG 集成；CLI `/kb` 仅保留命令示例。 |
| 安全模型 | [SECURITY.md](SECURITY.md) | 沙箱、命令执行、多实例锁、飞书凭证；多实例注册表细节见本文 §3.3。 |
| 架构与行为细节 | [README.md](../README.md) §架构概览 + `docs/ARCHITECTURE.md` 及各专题文档 | README 为架构概览 SSOT；各层细节以 ARCHITECTURE 为准。 |

飞书媒体与出站（JSON 键见 [`miniagent/resources/config.defaults.json`](../miniagent/resources/config.defaults.json) `feishu` 节；完整说明见 [FEISHU.md](FEISHU.md)）：

| JSON 路径 | 作用 |
|-----------|------|
| `feishu.media.run_agent` | 为真时，file/image/post 落盘后追加合成用户消息并跑 Agent。 |
| `feishu.media.silent_reply` | 为真时，落盘成功不向飞书发 `_send_reply`（CLI 镜像不受影响）。 |
| `feishu.reply_plain` | 默认 **关**；设为 `true` 时弱化 Markdown（仍为 `lark_md`）。 |
| `feishu.reply_target` | 默认 **`reply`**；`create` 为会话内新建消息。 |
| `feishu.reply_in_thread` | 与 `reply` 联用；未设置且入站 `thread_id` 非空时默认话题内回复。 |
| `feishu.card_action_router` | 默认 **开**；处理 `p2.card.action.trigger` 并将按钮 payload 投递到消息队列。 |
| `feishu.tools_explicit` / `feishu.tools_auto` | 显式 `true` 注册内置飞书工具；未设 `tools_explicit` 时由 `tools_auto`（默认开）且已配置 `secrets.feishu_*` 时在 init 自动注册。 |
| `feishu.doc.docx_url_prefix` | 创建云文档工具输出中带可分享 Web 链接的前缀。 |
| `feishu.receive_id_type` | 内置工具发 IM 时的 `receive_id_type`（`chat_id` / `open_id` / `union_id`）。 |
| `feishu.doc.folder_token` | 创建/列举云盘时默认父文件夹 token。 |

### 1.1 配置分层（User / Advanced / Internal）

[`miniagent/resources/config.defaults.json`](../miniagent/resources/config.defaults.json) 是随 wheel 发布的默认配置事实来源，**并非**每一项都是用户应理解的旋钮。文档与示例按三层划分：

| 层级 | 含义 | 文档位置 | 用户是否需了解 |
|------|------|----------|----------------|
| **User-facing** | 模型、凭据、路径、渠道行为、功能开关 | [README.md](../README.md) §配置、包内 defaults 顶部 User 层节 | 是 |
| **Advanced / Operator** | 超时、并发、记忆容量、飞书运维、Trace 保留 | 本文 §5、 [DEPLOYMENT.md](DEPLOYMENT.md)、各专题文档 | 按需 |
| **Internal** | 第三方 API 端点、节流毫秒、渲染边距、算法阈值 | 代码常量或 defaults 中的 dev 默认；**不**写入 user 示例 | 否 |

**典型 User-facing 节**：`secrets`、`model`、`paths`、`features`、以及 `feishu` / `agent` / `execution` 中的行为边界项。

**典型 Advanced 节**：`memory.*`、`dream.*`、`trace.*`、`feishu.websocket.*`、`feishu.card.*`、`agent.loop_detection.*`、`background_tasks.*`。

**典型 Internal（写入 [`core/constants.py`](../miniagent/core/constants.py)，不可通过 JSON 覆盖）**：`feishu.api_urls`、`feishu.patch.*`（流式卡片节流）、`clawhub.api_url`、`web_search.tavily_url`、`execution.*`（含 `EXECUTION_MAX_CONCURRENT_TOOLS` 工具并发硬上限）、`render.*`、`cli` 实现细节（`CLI_RAW_MARKDOWN` / `CLI_THINKING_RICH` 可被 ENV 或 `cli.*` 覆盖）、`browser.*`、`keyword_index.*` 算法阈值等。JSON 默认值种子（如 `DEFAULT_AGENT_MAX_TURNS`、`HISTORY_ARCHIVE_MAX_MESSAGES`）与包内 defaults 同步，用户 JSON 可覆盖对应键。输出前缀 emoji 见 [`types/error_prefix.py`](../miniagent/types/error_prefix.py)。

**加载机制**（见 [`json_config.py`](../miniagent/infrastructure/json_config.py)）：`defaults → user`（仅两层 JSON）。`secrets` 经 [`env_loader.py`](../miniagent/infrastructure/env_loader.py) 桥接到 `OPENAI_API_KEY` 等 SDK 变量，**不是**用户配置入口。`/config` 命令与 USER_GUIDE 仅展示 User 层子集。

**模型协议**：`model.wire_api` 默认 `chat_completions`，可显式设为 `responses`；统一 transport 负责消息、图片、工具调用、结束状态和流式事件转换。结构化 JSON 请求通过 `create_structured_completion` 路由：Responses 使用流式聚合，Chat 使用非流式 `json_object`。两条路径均返回统一的 `status`、output item 类型和 `incomplete_reason`。`model.user_agent` 只用于需要客户端标识白名单的兼容网关，空值继续使用 OpenAI SDK 默认值；含 CR/LF 的值会在客户端构造时拒绝。

**结构化控制链恢复**：分类、澄清、规划与反思的 Responses 请求从首次调用即使用流式聚合。首次请求不改写现有 reasoning、采样或 token 配置；第二次移除采样参数；第三次分类/`llm_json` 使用 low、规划使用 medium。明确的输出 token 截断才提高预算。中间恢复只记 INFO 和安全 trace，最终失败才记汇总 WARNING；不记录提示词、响应正文或凭据。

**执行器恢复**：Responses 执行流在尚未输出文本或工具调用时，可对网关泛化 400、429、5xx 与空完成事件做最多两次恢复；第二次移除 `temperature/top_p`，最后一次使用 medium。任何部分文本或工具调用都会关闭自动重试，防止重复动作。transport 会将没有 delta 的 `response.output_text.done` 作为正文，但若已消费相同位置的 delta 则不会重复拼接。

**凭据桥接**（Internal，非用户旋钮）：`config.user.json` → `secrets.*` → `OPENAI_API_KEY` / `FEISHU_APP_ID` 等，供第三方 SDK 读取。

### 1.2 环境变量分类

**用户配置**仅通过 JSON（`config.user.json` > 包内 defaults），不支持 `MINIAGENT_*` 覆盖配置项。环境变量分三类：

| 类别 | 说明 | 示例 | 文档 |
|------|------|------|------|
| **运维 / 调试类（仍有效）** | 启动行为、日志级别、特性开关，非 defaults 镜像 | `AGENT_DEBUG`、`MINIAGENT_TRACE_LOG_FILE`、`MINIAGENT_FEISHU_DOT_COMMANDS_FULL`、`MINIAGENT_DISABLE_SCHEDULED_TASKS`（设为 `1`/`true` 时禁用进程内定时任务调度；见 [CLI.md](CLI.md) `/schedule`） | [DEPLOYMENT.md](DEPLOYMENT.md)、[OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)、[FEISHU.md](FEISHU.md)、[CLI.md](CLI.md) |
| **路径覆盖类（仍有效）** | 覆盖状态目录或注册表根，不改变配置语义 | `MINIAGENT_PATHS_STATE_DIR`、`MINIAGENT_REGISTRY_STATE_DIR`、`MINIAGENT_PROJECT_DIR` | 本文 §3 |

凭据类变量（`OPENAI_API_KEY`、`FEISHU_APP_ID` 等）由 `secrets.*` 桥接，见上节。

---

## 2. 质量门禁（本地与 CI）

合并或发版前建议至少通过以下检查（与 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 一致）：

```bash
python -m pip install -e ".[dev,typing]"
python -m ruff check miniagent tests scripts
python -m compileall -q miniagent
python -m mypy miniagent
python scripts/check_architecture.py
python scripts/docstring_inventory.py --check
python scripts/check_docs.py
python -m bandit -q -r miniagent -x miniagent/skills/templates -lll
python -m pytest tests/ -q -m "not evaluation and not perf"
```

CI 说明：

- **`test` job**（矩阵 Python 3.10 / 3.12）：`pip install -e ".[dev,typing]"`，跑 `compileall`、Ruff、文档/docstring、Bandit、架构/函数长度、全包 Mypy、wheel 资源检查（仅 3.12）及离线非性能测试。
- **`test-feishu-extra` job**（仅 3.12）：`pip install -e ".[dev,feishu]"` 后再跑 `compileall`、`ruff` 与 `pytest -m "not evaluation"`，确保安装 `lark-oapi` 时仍通过（与主矩阵并行，不拖慢双版本安装）。
- **`test-mcp-extra` job**（仅 3.12）：`pip install -e ".[dev,mcp]"`，对官方 `mcp` SDK 做 `import` 冒烟，再跑 `compileall`、`ruff` 与 `pytest -m "not evaluation"`，防止 `[mcp]` extra 与代码导入漂移。

说明：

- **Ruff**：规则集见 `pyproject.toml`，包含 Pyflakes、isort、pyupgrade、Bugbear、async、性能与 C901；圈复杂度上限 15。
- **架构检查**：除依赖方向外，AST 零豁免要求生产函数/方法不超过 100 行；确需复杂状态机时应拆为小对象，而不是增加白名单。
- **compileall**：全包语法编译，可捕获部分「仅某测试未覆盖路径」的语法错误。
- **mypy**：CI 与本地均对整个 `miniagent` 包执行有类型函数体检查；可选 SDK 通过 Protocol/适配器隔离，不使用全局忽略掩盖内部错误。需安装 `.[dev,typing]`。
- **Pytest**：默认 `asyncio_mode = auto`；`tests/evaluation/` 下用例由 `conftest` 统一打上 `evaluation` marker，与主 CI 隔离；本地若要一次跑全量可执行 `python -m pytest tests/ -q`（含评测）。未装 `lark-oapi` 时部分飞书路径可能跳过；本地可改用 `pip install -e ".[dev,feishu]"` 与 CI 飞书 job 对齐。
- **覆盖率门禁**：CI 运行 `pytest-cov --cov-branch --cov-fail-under=80`；PR 使用 `diff-cover` 要求修改行覆盖率 ≥95%。实时数值以 CI 产物为准；测试代码必须验证行为，不能通过扩大 omit 或生成无断言测试达成。本地命令见 [INDEX.md](INDEX.md) §测试与质量。

### 2.1 测试责任矩阵

- Agent 阶段、模型协议与工具执行由 `test_agent_*`、`test_planner_*`、`test_executor_*` 和 `test_llm_*` 覆盖。
- CLI/TUI、历史与命令由 `test_cli_*`、`test_command_dispatch.py` 和 `test_help_markdown.py` 覆盖。
- 飞书接收、路由、卡片、Docx/Bitable/Drive 与降级由 `test_feishu_*` 覆盖。
- 会话、记忆、知识库、调度、Trace 和生命周期分别由同名测试模块覆盖。
- 新功能必须在同一变更中增加行为测试；删除测试前必须证明它与仍保留的测试断言完全重复。

可选增强（未默认纳入 CI，团队可自行约定）：

- 性能合成与剖析流程见 [PERFORMANCE.md](PERFORMANCE.md)；可选 workflow **Perf smoke**（`workflow_dispatch` / 定时）跑 `pytest -m perf` 与 `scripts/perf_profile_tracemalloc.py` 并上传带 commit SHA 的 artifact；离线对比两次 JSON 可用 `scripts/compare_perf_snapshots.py`。
- **可选 pre-commit**：仓库根 [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) 提供 `ruff` hook（路径 `miniagent`、`tests`）；本地执行 `pip install pre-commit && pre-commit install` 后随 commit 检查。
- **维护脚本清单**见 [scripts/README.md](../scripts/README.md)；自 v2.0.3 起，手工 verify 脚本已移除，性能回归用 `pytest -m perf` 与 `scripts/perf_profile_tracemalloc.py`。

---

## 3. 状态目录与测试隔离

**状态路径模型**（`miniagent/infrastructure/paths.py`）：

**Canonical 会话路径**（默认）：

```
{miniagent 包根}/workspaces/projects/{project_key}/sessions/<safe_session_id>/
```

下文与 `.gitignore` 中的 `workspaces/sessions/` 等为**简写**；完整解析规则见本表与 `resolve_project_state_dir()`。

| 路径 | 解析函数 | 默认位置 | 用途 |
|------|----------|----------|------|
| 项目 workspace | `resolve_state_dir()` / `resolve_project_state_dir()` | `{miniagent 包根}/workspaces/projects/{project_key}/` | 会话、路由、飞书锁、定时任务等业务状态（按 cwd 自动区分） |
| 全局实例注册表 | `resolve_registry_state_dir()` | `{miniagent 包根}/workspaces` | `instances/<id>/meta.json` + `heartbeat` |

- **启动时**：`python -m miniagent` 入口会将 `MINIAGENT_PROJECT_DIR` 设为启动时 cwd，并在未显式设置时写入 `MINIAGENT_PATHS_STATE_DIR`（项目 workspace 根，位于共用 `workspaces/projects/{project_key}/`）。
- **路径确定性**：解析只依赖显式环境变量、绝对 `paths.state_dir` 与 canonical 注册表，不扫描磁盘残留目录。
- **推荐**：测试或并行部署时用 `MINIAGENT_PATHS_STATE_DIR` 指定独立项目数据目录；注册表不受该变量影响（测试可用 `MINIAGENT_REGISTRY_STATE_DIR` 覆盖）。
- **一目录一实例**：见 §3.3。
- 部分路径见 `.gitignore`，如 `workspaces/sessions/`（即 `{paths.state_dir}/sessions/` 的简写）、`**/*.lock`。

### 3.1 `workspaces/` 与 Git 跟踪政策

**运行时生成物默认不入库**：`.gitignore` 已排除 `workspaces/instances/`、`workspaces/sessions/`（canonical：`{paths.state_dir}/sessions/`，默认 `{miniagent}/workspaces/projects/{project_key}/sessions/`）、`workspaces/memory/`、`workspaces/scheduled_tasks/`（定时任务表 `tasks.json`，路径为 `{paths.state_dir}/scheduled_tasks/tasks.json`，默认 `workspaces/scheduled_tasks/`）、`workspaces/self_opt/`（自我优化提案与分析报告）、`workspaces/logs/`（Trace 日志）、`workspaces/keyword-index.json`、`workspaces/perf*.jsonl`、`workspaces/feishu_inbound_owner.json`、`workspaces/feishu/`（含 WebSocket 去重等）、`**/*.lock`、`workspaces/cli/` 等，避免把本机 PID、会话历史、记忆索引、对话落盘、飞书去重状态提交到远程。

若上述路径被版本跟踪，可在确认无团队依赖后执行 `git rm --cached <路径>` 并保留 `.gitignore` 规则。配置形状以包内 defaults 的 `_config_guide` 与分层节为准；日常开发建议在 `config.user.json` 将 `paths.state_dir` 设为仓库外目录。

**提交前建议再看一眼 `git status`**：不应把 `__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`*.egg-info/` 等缓存或打包元数据加入版本库（勿对这类路径使用 `git add -f`）。`git clean -fdX` 会删除**所有**已忽略路径（含本地 **`config.user.json`**），执行前请备份密钥；更稳妥做法是只手动删缓存目录。勿用小写 `git clean -fdx`，以免删掉未跟踪的源码。详见 [CONTRIBUTING.md](CONTRIBUTING.md)「提交前仓库卫生」。

### 3.2 可选离线测评产物

若使用 `tests/evaluation/`（见 [tests/evaluation/README_API_PERF.md](../tests/evaluation/README_API_PERF.md)）：

| 类型 | Git 策略 |
|------|----------|
| **应提交** | `tests/evaluation/**/*.py`、`conftest.py`、小体积 `test_cases/*.json`、评测脚本等非密钥文本 |
| **勿提交** | `tests/evaluation/runners/trajectories/`、`**/evaluation_results.json`、生成到 `docs/` 的报告或导出 JSON |

**轨迹 JSON、聚合评分与 HTML 报告**体积大且环境相关；对话片段中还可能误粘贴 **API Key**，即使已在 `.gitignore` 中列出，也**不要**使用 `git add -f` 强行入库。根目录 `.gitignore` 已忽略 `tests/evaluation/runners/trajectories/`、`tests/evaluation/**/evaluation_results.json`、`docs/EVALUATION_REPORT.html`、`docs/evaluation_results.json` 等。

### 3.3 多实例注册表

实现位于 [`miniagent/infrastructure/instance.py`](../miniagent/infrastructure/instance.py)。注册表根目录由 `resolve_registry_state_dir()` 解析（默认 `{miniagent 包根}/workspaces`），与项目 `paths.state_dir` **分离**。

**目录结构**：

```
workspaces/instances/
├── 1/
│   ├── meta.json    # pid、instance_id、mode、active_sessions、project_dir 等
│   └── heartbeat    # 心跳时间戳（仅观测）
└── 2/
    └── ...
```

**存活判定**：是否在列表中显示、是否删除磁盘目录，均以 **操作系统 PID 是否存在** 为准（`is_process_running`）。`register()` 在分配新 `instance_id` **之前** 会扫描并删除 PID 已失效的目录；**不会**向其它进程发送终止信号。`heartbeat` 文件仍会更新，便于人工排查；**不参与**存活判定，避免心跳写入滞后导致误删仍在运行的实例。

**一目录一实例**：同一 `project_dir`（启动 cwd）仅允许一个存活 Agent；冲突时启动失败并提示 `python -m miniagent --stop`。不同 cwd 可并行，各自独立 workspace。

**实例 mode**：`cli`（仅 CLI 主循环）或 `both`（CLI + 飞书连接已启用）；不存在无 CLI 的独立飞书进程入口。

**CLI 管理**：

```bash
python -m miniagent --stop          # 交互停止当前或其它实例
python -m miniagent --stop --all    # 停止全部
```

进程内也可用 `/instance list`、`/instance stop <id>`（见 [CLI.md](CLI.md)）。

**`--stop --state-dir`**：参数指向**实例注册表根**（含 `instances/` 子目录），默认 `{miniagent}/workspaces`；**不是**项目会话数据目录（`projects/<key>/`）。该参数用于显式检查或停止指定注册表中的实例。

**会话互斥**：每个会话工作空间 `.lock` 文件（PID）防止多实例抢同一 session；定时任务调度锁见 `scheduled_tasks/*.lock`（详见 [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」）。

**测试隔离**：`reset_instance_registry_for_tests()` 与 `conftest` fixture 用于重置进程级注册表单例。

---

## 4. 自我优化子系统

自我优化基于 Trace 运行指标与代码静态分析生成提案；**配置项、阈值、CLI/API 与操作步骤**见 **[SELF_OPT.md](SELF_OPT.md)**（SSOT）。本节仅保留工程侧要点：

- 提案与报告默认写入 `workspaces/self_opt/`（或 `self_optimization.proposal_output_dir`；相对 miniagent 包根，**不在** `{paths.state_dir}` 下），**不入库**（见 §3.1）。
- 运行分析依赖 Trace（§5）；`self_optimization.runtime_analysis_enabled` 为真时需 `trace.enabled: true`。
- 默认 `auto_apply: false`，仅生成提案；自动执行与风险上限见 SELF_OPT §配置。

---

## 5. Trace 系统（全链路监控）

Trace 系统为自我优化提供运行数据源，同时支持外部 APM 接入。

### 5.1 架构设计

```
miniagent.infrastructure.tracing
├── emit_trace(event)              # 派发事件到钩子列表
├── register_trace_hook(hook)      # 注册回调钩子
├── clear_trace_hooks()            # 清空钩子（测试隔离）
├── auto_register_trace_file_hook() # 自动注册文件持久化钩子
├── get_actual_trace_file()        # 获取当前进程实际写入路径
├── get_actual_trace_file()        # 获取当前进程实际写入文件
└── get_trace_writer_stats()       # 获取队列深度、写入/丢弃计数等 writer 指标

miniagent.infrastructure.trace_events
├── 事件类型常量（EVENT_LLM_REQUEST 等）
└── 事件构建函数（make_error_event 等）

miniagent.infrastructure.trace_stats
├── get_trace_files()              # 枚举当天全部 pid 分片
├── load_trace_events()            # 加载并过滤事件
├── compute_tool_stats()           # 工具统计
├── compute_llm_stats()            # LLM 统计
├── compute_error_stats()          # 错误统计
└── generate_daily_report()        # 每日报告
```

### 5.2 事件类型规范

| 常量 | 类型字符串 | 用途 |
|------|-----------|------|
| `EVENT_LLM_REQUEST` | `llm.request` | LLM 请求开始（model、message_count、tool_count） |
| `EVENT_LLM_RESPONSE` | `llm.response` | LLM 响应结束（usage、has_tool_calls） |
| `EVENT_TOOL_START` | `tool.start` | 工具执行开始 |
| `EVENT_TOOL_END` | `tool.end` | 工具执行结束（duration_ms、success） |
| `EVENT_TOOL_ERROR` | `tool.error` | 工具错误（error_type、is_user_error） |
| `EVENT_ERROR_COLLECT` | `error.collect` | 错误收集（统一错误事件） |
| `EVENT_PROPOSAL_*` | `proposal.*` | 自我优化提案生命周期 |

### 5.3 标准事件字段

所有事件建议包含以下标准字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 事件类型（必填） |
| `ts` | string ISO 8601 | 时间戳（由 `emit_trace` 自动添加） |
| `session_key` | string | 会话标识 |
| `phase` | "plan" / "exec" | 执行阶段 |
| `duration_ms` | int | 时延（毫秒） |
| `success` | bool | 是否成功 |
| `error_type` | string | 错误类型（可选） |
| `is_user_error` | bool | 是否用户误用 |

### 5.4 自动持久化

配置 `miniagent/resources/config.defaults.json`：

```json
{
  "trace": {
    "enabled": true,
    "output_dir": "workspaces/logs",
    "retention_days": 7,
    "writer_batch_interval": 0.1,
    "writer_batch_size": 50,
    "writer_queue_max_size": 10000,
    "writer_overflow_policy": "drop_oldest",
    "record_payload": "metrics_only"
  }
}
```

或在 `config.user.json` 中启用 Trace 持久化：

```json
{
  "trace": {
    "enabled": true,
    "output_dir": "workspaces/logs"
  }
}
```

`get_actual_trace_file()` 返回异步 writer 实际写入的 `trace-{YYYY-MM-DD}-pid{pid}.jsonl`，避免多进程同时追加同一文件。`trace_stats.get_trace_files(date)` 和 `load_trace_events(date)` 会聚合同一天的基础文件与全部 pid 分片，日报和自我优化分析不会漏读历史格式或真实运行分片。

writer 使用有界队列保护内存，并按 `writer_batch_interval` / `writer_batch_size` 聚合真实批次；关闭状态只排空已接收队列，不再等待批次窗口。默认 `writer_queue_max_size=10000`、`writer_overflow_policy=drop_oldest`；队列满时不阻塞主流程，而是按策略丢弃事件。`get_trace_writer_stats()` 和 `shutdown_trace_writer()` 的最终返回值包含 `emitted_count`、`written_count`、`dropped_count`、`serialization_error_count` 与 `write_error_count`。这些是 writer 内部状态，不通过 `emit_trace()` 递归上报。

真实 API 压测和默认 trace 策略采用 `record_payload: "metrics_only"`：trace 只应保存模型、时延、token、状态、会话/请求关联 ID、错误类型等指标，不落完整 prompt、response 或 API key。

### 5.5 统计分析

运行分析器从 Trace 文件提取指标：

```python
from miniagent.infrastructure.trace_stats import generate_daily_report

report = generate_daily_report(date="2026-06-05")

# report 结构：
{
  "date": "2026-06-05",
  "total_events": 1234,
  "sessions": 10,
  "llm": {
    "request_count": 10,
    "response_count": 10,
    "total_tokens": {"prompt": 5000, "completion": 2000, "cached": 1500, "reasoning": 300},
    "avg_duration_ms": 500,
    "p50_duration_ms": 450,
    "p95_duration_ms": 900,
    "by_phase": {"plan": {"avg_tools": 0, "cached_token_rate": 0.3}},
  },
  "tools": {
    "tools": {"read_file": {"count": 10, "avg_ms": 50, "success_rate": 1.0}},
    "slow_tools": [{"name": "web_search", "avg_ms": 2000}],
    "failed_tools": [{"name": "read_file", "success_rate": 0.95}],
  },
  "errors": [{"type": "TimeoutError", "count": 3, "tools": ["web_search"]}],
}
```

### 5.6 测试隔离

测试用例中可清空钩子避免污染：

```python
from miniagent.infrastructure.tracing import clear_trace_hooks

def test_trace():
    clear_trace_hooks()
    # ... 测试逻辑 ...
```

### 5.7 与自我优化集成

Trace 事件作为自我优化数据源：

```python
from miniagent.core.self_opt import RuntimeAnalyzer

analyzer = RuntimeAnalyzer()
report = analyzer.analyze(date="2026-06-05")

# report 从 trace-stats 和 activity-log 合并数据
# tools：工具性能指标
# llm：LLM 调用统计
# errors：错误汇总
# issues：问题标记（慢工具、高失败率、高频错误）
```

---

## 6. 状态清理与保留

### 6.1 Trace 文件清理

```python
from miniagent.infrastructure.trace_stats import cleanup_old_traces

# 删除超过 7 天的 trace 文件；兼容 trace-YYYY-MM-DD.jsonl 与 trace-YYYY-MM-DD-pid*.jsonl
deleted = cleanup_old_traces(retention_days=7)
```

### 6.2 提案文件清理

```python
from miniagent.core.self_opt.proposal_store import ProposalStore

# 删除超过 30 天的提案文件
deleted = ProposalStore.cleanup_old_proposals(retention_days=30)
```

---

## 7. 常用术语（短表）

| 术语 | 含义 |
|------|------|
| `session_key` | 会话逻辑键（如 `default`、`feishu:oc_xxx`）；记忆/锁/文件按会话隔离 |
| `project_key` | 由启动 cwd 等确定性推导的项目分区名；状态落在 `workspaces/projects/{project_key}/` |
| `__cli__` | CLI 通道在 `ChannelRouter` 上的绑定标识 |
| `oc_` / `ou_` | 飞书群 `chat_id` / 用户 `open_id` 常见前缀；CLI `/session switch oc_xxx` 会规范为 `feishu:oc_xxx` |
| User / Advanced / Internal | 配置分层：用户 JSON、运维调优、不可 JSON 覆盖的代码常量（见 §1.1） |
| 包版本 vs schema | `miniagent.__version__`（发版）与 `config.defaults.json` 顶层 `version`（defaults schema）双轨 |

## 8. 相关文档

- [SELF_OPT.md](SELF_OPT.md) — 自我优化系统详解
- [CLI.md](CLI.md) — CLI 命令手册（自我优化命令）
- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构
- [CONTRIBUTING.md](CONTRIBUTING.md) — 贡献指南（含扩展开发与 API 示例）
- [PERFORMANCE.md](PERFORMANCE.md) — 性能测试与调优
