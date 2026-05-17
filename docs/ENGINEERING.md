# 软件工程实践与仓库卫生

> Mini Agent Python | 版本: 2.0.2 | 与 `miniagent.__version__` 对齐

本文档汇总本仓库在**可维护性、可重复构建、安全与协作**上的约定，作为 [CONTRIBUTING.md](CONTRIBUTING.md) 的补充：后者偏「如何写代码」，本文偏「仓库与发布如何保持健康」。

---

## 1. 单一事实来源（Single Source of Truth）

| 主题 | 权威位置 | 说明 |
|------|----------|------|
| 可安装包名与源码布局 | `pyproject.toml` → `[tool.setuptools.packages.find]` | 仅打包 `miniagent*`；不再维护顶层 `src` 作为可导入包。 |
| 版本号 | `miniagent/__init__.py` 中 `__version__` | `pyproject.toml` 通过 `dynamic.version` 读取；发版时与 `CHANGELOG.md`、本文档顶部标语一并更新。 |
| 依赖声明 | `pyproject.toml` `[project]` / `optional-dependencies` | 不使用根目录 `requirements.txt`；运行时依赖与可选组（`dev`（含 `pytest-cov`）、`feishu`、`browser`、`mcp`、`cli`、`typing`（`mypy` 试点））集中在此。 |
| 环境变量说明 | 仓库根 `.env.example` | 复制为 `.env` 后本地填写；**勿将含真实密钥的 `.env` 提交入库**（见 `.gitignore`）。 |
| 定时任务环境变量 | `.env.example` + [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」 | 用户面向摘要见 [USER_GUIDE.md](USER_GUIDE.md) §8；运维见 [DEPLOYMENT.md](DEPLOYMENT.md) |
| 架构与行为细节 | `docs/ARCHITECTURE.md` 及各专题文档 | README 只做索引与快速上手；深度说明以 `docs/` 为准。 |

飞书媒体（与 [FEISHU.md](FEISHU.md) 正文一致，便于检索）：

| 变量 | 作用 |
|------|------|
| `MINIAGENT_FEISHU_MEDIA_RUN_AGENT` | 为真时，file/image/post 落盘后追加合成用户消息并跑 Agent。 |
| `MINIAGENT_FEISHU_MEDIA_SILENT_REPLY` | 为真时，落盘成功不向飞书发 `_send_reply`（CLI 镜像不受影响）。 |

飞书出站、卡片与可选工具（完整说明见 [FEISHU.md](FEISHU.md) 环境变量表与架构节）：

| 变量 | 摘要 |
|------|------|
| `MINIAGENT_FEISHU_REPLY_TARGET` | `create`（默认）或 `reply`；非法值回退为 `create`。 |
| `MINIAGENT_FEISHU_REPLY_IN_THREAD` | 与 `reply` 联用；未设置且入站 `thread_id` 非空时默认话题内回复（见 FEISHU）。 |
| `MINIAGENT_FEISHU_CARD_ACTION_ROUTER` | 为真时处理 `p2.card.action.trigger`，将按钮 payload 投递到同一消息队列。 |
| `MINIAGENT_FEISHU_TOOLS` | 为真时注册内置飞书 IM/Doc 工具（需凭证与开放平台权限）。 |
| `MINIAGENT_FEISHU_TOOLS_AUTO` | 为真且已配置 App ID/Secret 时，若未设置 `MINIAGENT_FEISHU_TOOLS` 则自动注册同上工具；**在进程 init 注册内置工具时生效**，不等待飞书 WebSocket。 |
| `FEISHU_DOCX_URL_PREFIX` / `MINIAGENT_FEISHU_DOCX_URL_PREFIX` | 创建云文档工具输出中带可分享 Web 链接的前缀（租户域名须与飞书控制台一致）。 |
| `MINIAGENT_FEISHU_RECEIVE_ID_TYPE` | 内置工具发 IM 时的 `receive_id_type`（`chat_id` / `open_id` / `union_id`）；非 `chat_id` 时默认 `receive_id` 为入站发送者 ID（见 [FEISHU.md](FEISHU.md)）。 |
| `FEISHU_DEFAULT_DOC_FOLDER_TOKEN` / `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` | 创建云文档时默认父文件夹 token。 |

---

## 2. 质量门禁（本地与 CI）

合并或发版前建议至少通过以下检查（与 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 一致）：

```bash
python -m pip install -e ".[dev,typing]"
python -m ruff check miniagent tests
python -m compileall -q miniagent
python -m mypy miniagent/types
python -m pytest tests/ -q -m "not evaluation"
```

CI 说明：

- **`test` job**（矩阵 Python 3.10 / 3.12）：`pip install -e ".[dev,typing]"`，跑 `compileall`、`ruff`、`mypy miniagent/types`、`pytest -m "not evaluation"`。
- **`test-feishu-extra` job**（仅 3.12）：`pip install -e ".[dev,feishu]"` 后再跑 `compileall`、`ruff` 与 `pytest -m "not evaluation"`，确保安装 `lark-oapi` 时仍通过（与主矩阵并行，不拖慢双版本安装）。
- **`test-mcp-extra` job**（仅 3.12）：`pip install -e ".[dev,mcp]"`，对官方 `mcp` SDK 做 `import` 冒烟，再跑 `compileall`、`ruff` 与 `pytest -m "not evaluation"`，防止 `[mcp]` extra 与代码导入漂移。
- **`evaluation` job**：仅在 **手动 `workflow_dispatch`** 时运行，`pytest -m evaluation`；可在仓库 Secrets 中配置 `OPENAI_API_KEY`、`TAVILY_API_KEY` 等供依赖网络的评测用例使用。

说明：

- **Ruff**：风格、导入顺序、pyupgrade 风格（`UP`）与部分静态问题；规则集见 `pyproject.toml` `[tool.ruff]` / `[tool.ruff.lint]`（含 `E4`、`E7`、`E9`、`F`、`I`、`UP`；`E402` 对部分延后 import 忽略）。
- **compileall**：全包语法编译，可捕获部分「仅某测试未覆盖路径」的语法错误。
- **mypy（试点）**：`python -m mypy miniagent/types`；与 `test` CI job 一致，需安装 `.[dev,typing]`。
- **Pytest**：默认 `asyncio_mode = auto`；`tests/evaluation/` 下用例由 `conftest` 统一打上 `evaluation` marker，与主 CI 隔离；本地若要一次跑全量可执行 `python -m pytest tests/ -q`（含评测）。未装 `lark-oapi` 时部分飞书路径可能跳过；本地可改用 `pip install -e ".[dev,feishu]"` 与 CI 飞书 job 对齐。
- **覆盖率（可选）**：`pip install -e ".[dev]"` 已包含 `pytest-cov`；本地示例：`python -m pytest tests/ -q -m "not evaluation" --cov=miniagent --cov-report=term-missing`。**默认 CI 不启用** `--cov`，以免拖慢矩阵；团队发版前可自选执行。

可选增强（未默认纳入 CI，团队可自行约定）：

- 性能合成与剖析流程见 [PERFORMANCE.md](PERFORMANCE.md)；可选 workflow **Perf smoke**（`workflow_dispatch` / 定时）跑 `pytest -m perf` 与 `scripts/perf_profile_tracemalloc.py` 并上传带 commit SHA 的 artifact；离线对比两次 JSON 可用 `scripts/compare_perf_snapshots.py`。
- **可选 pre-commit**：仓库根 [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) 提供 `ruff` hook（路径 `miniagent`、`tests`）；本地执行 `pip install pre-commit && pre-commit install` 后随 commit 检查。

---

## 3. 状态目录与测试隔离

- **默认**：Agent 将实例心跳、会话、锁等写入仓库下 `workspaces/`（部分路径见 `.gitignore`，如 `workspaces/sessions/`、`**/*.lock`）。
- **推荐**：开发与 CI 设置 **`MINI_AGENT_STATE`** 指向临时目录，避免污染本机数据或与并行运行冲突（示例见 `CONTRIBUTING.md` 与 `.env.example` 注释）。
- **语义**：多实例注册、PID 判定与清理规则见 [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md)。

### 3.1 `workspaces/` 与 Git 跟踪政策

**运行时生成物默认不入库**：`.gitignore` 已排除 `workspaces/instances/`、`workspaces/sessions/`、`workspaces/memory/`、`workspaces/scheduled_tasks/`（定时任务表 `tasks.json`，与 README「`MINI_AGENT_STATE/scheduled_tasks/tasks.json`」一致；未设置 `MINI_AGENT_STATE` 时默认为仓库下 `workspaces/scheduled_tasks/`）、`workspaces/keyword-index.json`、`workspaces/perf*.jsonl`、`workspaces/feishu_inbound_owner.json`、`workspaces/feishu/`（含 WebSocket 去重等）、`**/*.lock`、`workspaces/cli/` 等，避免把本机 PID、会话历史、记忆索引、对话落盘、飞书去重状态提交到远程。

若历史上曾将上述路径纳入版本跟踪，可在确认无团队依赖后执行 `git rm --cached <路径>` 并保留 `.gitignore` 规则。需要随仓库携带的**非敏感**结构示例，请放在 `docs/examples/` 等显式文档化目录。日常开发仍建议使用 `MINI_AGENT_STATE` 将状态迁出仓库。

**提交前建议再看一眼 `git status`**：不应把 `__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`*.egg-info/` 等缓存或打包元数据加入版本库（勿对这类路径使用 `git add -f`）。`git clean -fdX` 会删除**所有**已忽略路径（含本地 **`.env`**），执行前请备份密钥；更稳妥做法是只手动删缓存目录。勿用小写 `git clean -fdx`，以免删掉未跟踪的源码。详见 [CONTRIBUTING.md](CONTRIBUTING.md)「提交前仓库卫生」。

### 3.2 可选离线测评产物

若使用 `tests/evaluation/`（见 [EVALUATION_LOCAL.md](EVALUATION_LOCAL.md)）：

| 类型 | Git 策略 |
|------|----------|
| **应提交** | `tests/evaluation/**/*.py`、`conftest.py`、小体积 `test_cases/*.json`、评测脚本等非密钥文本 |
| **勿提交** | `tests/evaluation/runners/trajectories/`、`**/evaluation_results.json`、生成到 `docs/` 的报告或导出 JSON |

**轨迹 JSON、聚合评分与 HTML 报告**体积大且环境相关；对话片段中还可能误粘贴 **API Key**，即使已在 `.gitignore` 中列出，也**不要**使用 `git add -f` 强行入库。根目录 `.gitignore` 已忽略 `tests/evaluation/runners/trajectories/`、`tests/evaluation/**/evaluation_results.json`、`docs/EVALUATION_REPORT.html`、`docs/evaluation_results.json` 等。

---

## 4. 安全与密钥

- 密钥优先通过环境变量或本地 `.env` 注入；代码库中不出现真实 token（含 **OpenAI**、**Tavily** (`TAVILY_API_KEY` / `WEB_SEARCH_API_KEY`)、飞书 Secret 等）。
- 可选外部 JSON（`MINIAGENT_CONFIG`）可将 `apiKey` 回填至进程环境，风险与清单见 [SECURITY.md](SECURITY.md) §6。
- 工具执行与文件访问受 [SECURITY.md](SECURITY.md) 所述沙箱与策略约束；部署面见 [DEPLOYMENT.md](DEPLOYMENT.md)。
- **推送前自检（建议）**：勿提交 `.env`；`git diff --cached` 抽查是否误入密钥；可用 `rg` 等搜索疑似模式（如 `tvly-`、`sk-[A-Za-z0-9]{20,}`）并与占位符区分。仓库可在 GitHub 开启 Secret scanning / Push protection（在网页端配置）。

---

## 5. 文档维护清单

大范围重构或发版时建议核对：

1. `miniagent/__init__.py` 的 `__version__` 与 `CHANGELOG.md`、下列 **带版本标语** 的 `docs/*.md` 一致（标语格式建议：`> Mini Agent Python | 版本: x.y.z | …` 或 INDEX 的「与 `miniagent.__version__` 对齐」行；若页眉仅写「与 `miniagent.__version__` 对齐」而无具体 semver，发版时核对语义一致即可）：
   - [ARCHITECTURE.md](ARCHITECTURE.md)、[INDEX.md](INDEX.md)、[ENGINEERING.md](ENGINEERING.md)、[CONTRIBUTING.md](CONTRIBUTING.md)
   - [DEPLOYMENT.md](DEPLOYMENT.md)、[MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)、[SECURITY.md](SECURITY.md)、[INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md)
   - [CLI.md](CLI.md)、[FEISHU.md](FEISHU.md)、[SELF_OPT.md](SELF_OPT.md)、[CHANNEL_BINDING.md](CHANNEL_BINDING.md)、[CYBERNETICS_PLAN.md](CYBERNETICS_PLAN.md)、[USER_GUIDE.md](USER_GUIDE.md)、[EVALUATION_LOCAL.md](EVALUATION_LOCAL.md)（若文内写明版本号须与 `__version__` 一致）
   - [PERFORMANCE.md](PERFORMANCE.md)（页眉与版本对齐语义时一并核对）
2. 欢迎界面：`miniagent.engine.welcome.get_version()` 必须与 `miniagent.__version__` 同源（勿依赖 `pyproject.toml` 静态 `version` 字段）。
3. [INDEX.md](INDEX.md) 中目录树与仓库实际文件一致（含 `core/openai_client.py`、`memory/defaults.py` 等）。
4. README 中的命令与测试说明：若需核对用例数量，以本地或 CI 的 `pytest tests/ --collect-only -q` 输出为准（避免在 README 硬编码条数导致漂移）。
5. 行为变更同步 `ARCHITECTURE.md` 或对应专题文档（如 `CHANNEL_BINDING.md`、`MEMORY_SYSTEM.md`）。
6. **[architecture.drawio](architecture.drawio)** 与 `ARCHITECTURE.md` 分层与主数据流一致（入口 `compat`、组合根 `RuntimeContext`、通道路由、记忆注入方式、可选 MCP/定时任务）；`instance.py` 单元格为 **PID 存活** 语义（非心跳超时清理）；`scheduled_tasks` 含 `cron.py` / `file_lock.py`；发版或大架构变更时一并打开核对，页脚测试数以 `pytest tests/ --collect-only -q` 为准。
7. **[DEPLOYMENT.md](DEPLOYMENT.md)**：定时任务路径/备份、`MINI_AGENT_STATE` 与多实例 PID 清理表述与 INSTANCE_REGISTRY 一致。
8. **大批量增补或调整 docstring 后**：在本地执行 `python -m ruff check miniagent tests` 与 spot-check（避免行长、引号或无意改坏字符串）；风格约定见 [CONTRIBUTING.md](CONTRIBUTING.md)「文档字符串（docstring）规范」。

---

## 6. 相关链接

| 文档 | 用途 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、编码规范、测试约定 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层架构与数据流 |
| [INDEX.md](INDEX.md) | 全部文档索引 |
| [USER_GUIDE.md](USER_GUIDE.md) | 零基础使用指南 |
| [EVALUATION_LOCAL.md](EVALUATION_LOCAL.md) | 可选本地离线测评与 Git 忽略约定 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本历史 |
| [PERFORMANCE.md](PERFORMANCE.md) | 性能 KPI、合成冒烟与基线 |
