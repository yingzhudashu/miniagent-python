# 软件工程实践与仓库卫生

> Mini Agent Python | 版本: 2.0.3 | 与 `miniagent.__version__` 对齐 | 未发版行为见 [CHANGELOG](../CHANGELOG.md) `[Unreleased]`

本文档汇总本仓库在**可维护性、可重复构建、安全与协作**上的约定，作为 [CONTRIBUTING.md](CONTRIBUTING.md) 的补充：后者偏「如何写代码」，本文偏「仓库与发布如何保持健康」。

---

## 1. 单一事实来源（Single Source of Truth）

| 主题 | 权威位置 | 说明 |
|------|----------|------|
| 可安装包名与源码布局 | `pyproject.toml` → `[tool.setuptools.packages.find]` | 仅打包 `miniagent*`；不再维护顶层 `src` 作为可导入包。 |
| 版本号 | `miniagent/__init__.py` 中 `__version__` | `pyproject.toml` 通过 `dynamic.version` 读取；发版时与 `CHANGELOG.md`、本文档顶部标语一并更新。 |
| 依赖声明 | `pyproject.toml` `[project]` / `optional-dependencies` | 不使用根目录 `requirements.txt`；运行时依赖与可选组（`dev`（含 `pytest-cov`）、`feishu`、`browser`、`mcp`、`cli`、`typing`（`mypy` 试点））集中在此。 |
| 配置说明 | `config.defaults.json` + [ENV_REFERENCE.md](ENV_REFERENCE.md) | 复制为 `config.user.json` 后本地填写；**勿将含真实密钥的 `config.user.json` 提交入库**（见 `.gitignore`）。 |
| 定时任务配置 | `config.defaults.json` + [ARCHITECTURE.md](ARCHITECTURE.md)「定时任务子系统」 | 用户面向摘要见 [USER_GUIDE.md](USER_GUIDE.md) §8；运维见 [DEPLOYMENT.md](DEPLOYMENT.md) |
| 自我优化配置 | `config.defaults.json` → `self_optimization` 配置节 | 提案持久化路径、自动执行开关、风险等级上限等；详见 [SELF_OPT.md](SELF_OPT.md) |
| Trace 系统配置 | `config.defaults.json` → `trace` 配置节 | 持久化开关、输出目录、保留天数等；详见下文 §5 |
| 架构与行为细节 | `docs/ARCHITECTURE.md` 及各专题文档 | README 只做索引与快速上手；深度说明以 `docs/` 为准。 |

飞书媒体（与 [FEISHU.md](FEISHU.md) 正文一致，便于检索）：

| 变量 | 作用 |
|------|------|
| `MINIAGENT_FEISHU_MEDIA_RUN_AGENT` | 为真时，file/image/post 落盘后追加合成用户消息并跑 Agent。 |
| `MINIAGENT_FEISHU_MEDIA_SILENT_REPLY` | 为真时，落盘成功不向飞书发 `_send_reply`（CLI 镜像不受影响）。 |

飞书出站、卡片与可选工具（完整说明见 [FEISHU.md](FEISHU.md) 环境变量表与架构节）：

| 变量 | 摘要 |
|------|------|
| `MINIAGENT_FEISHU_REPLY_PLAIN` | 默认 **关**（设为 `1`/`true` 时开启纯文本模式）；无法识别的非空取值视为关。 |
| `MINIAGENT_FEISHU_REPLY_TARGET` | 默认 **`reply`**；`create` 为会话内新建消息；非法值按 `create` 处理。 |
| `MINIAGENT_FEISHU_REPLY_IN_THREAD` | 与 `reply` 联用；未设置且入站 `thread_id` 非空时默认话题内回复（见 FEISHU）。 |
| `MINIAGENT_FEISHU_CARD_ACTION_ROUTER` | 默认 **开**；处理 `p2.card.action.trigger` 并将按钮 payload 投递到同一消息队列；无法识别的非空取值视为关。 |
| `MINIAGENT_FEISHU_TOOLS` | 为真时注册内置飞书 IM/Doc 工具；已设置但取值无法识别时**关闭**（不落入 AUTO）。 |
| `MINIAGENT_FEISHU_TOOLS_AUTO` | 默认 **开**：未设置 `MINIAGENT_FEISHU_TOOLS` 且已配置 App ID/Secret 时在进程 init 自动注册；不等待 WebSocket。 |
| `FEISHU_DOC_FOLDER_FALLBACK_ROOT_META` | 默认 **开**；无 `folder_token` 时尝试根目录元数据 API（`0`/`false` 关闭）。 |
| `MINIAGENT_FEISHU_DOCX_URL_PREFIX` | 创建云文档工具输出中带可分享 Web 链接的前缀（租户域名须与飞书控制台一致）。 |
| `MINIAGENT_FEISHU_RECEIVE_ID_TYPE` | 内置工具发 IM 时的 `receive_id_type`（`chat_id` / `open_id` / `union_id`）；非 `chat_id` 时默认 `receive_id` 为入站发送者 ID（见 [FEISHU.md](FEISHU.md)）。 |
| `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` | 创建/列举云盘时默认父文件夹 token。 |

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
- **推荐**：开发与 CI 设置 **`MINIAGENT_PATHS_STATE_DIR`** 指向临时目录，避免污染本机数据或与并行运行冲突（示例见 `CONTRIBUTING.md` 与 `config.defaults.json` 注释）。
- **语义**：多实例注册、PID 判定与清理规则见 §3.3。

### 3.1 `workspaces/` 与 Git 跟踪政策

**运行时生成物默认不入库**：`.gitignore` 已排除 `workspaces/instances/`、`workspaces/sessions/`、`workspaces/memory/`、`workspaces/scheduled_tasks/`（定时任务表 `tasks.json`，与 README「`MINIAGENT_PATHS_STATE_DIR/scheduled_tasks/tasks.json`」一致；未设置 `MINIAGENT_PATHS_STATE_DIR` 时默认为仓库下 `workspaces/scheduled_tasks/`）、`workspaces/self_opt/`（自我优化提案与分析报告）、`workspaces/logs/`（Trace 日志）、`workspaces/keyword-index.json`、`workspaces/perf*.jsonl`、`workspaces/feishu_inbound_owner.json`、`workspaces/feishu/`（含 WebSocket 去重等）、`**/*.lock`、`workspaces/cli/` 等，避免把本机 PID、会话历史、记忆索引、对话落盘、飞书去重状态提交到远程。

若历史上曾将上述路径纳入版本跟踪，可在确认无团队依赖后执行 `git rm --cached <路径>` 并保留 `.gitignore` 规则。需要随仓库携带的**非敏感**结构示例，请放在 `docs/examples/` 等显式文档化目录。日常开发仍建议使用 `MINIAGENT_PATHS_STATE_DIR` 将状态迁出仓库。

**提交前建议再看一眼 `git status`**：不应把 `__pycache__/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、`*.egg-info/` 等缓存或打包元数据加入版本库（勿对这类路径使用 `git add -f`）。`git clean -fdX` 会删除**所有**已忽略路径（含本地 **`config.user.json`**），执行前请备份密钥；更稳妥做法是只手动删缓存目录。勿用小写 `git clean -fdx`，以免删掉未跟踪的源码。详见 [CONTRIBUTING.md](CONTRIBUTING.md)「提交前仓库卫生」。

### 3.2 可选离线测评产物

若使用 `tests/evaluation/`（见 §3.4）：

| 类型 | Git 策略 |
|------|----------|
| **应提交** | `tests/evaluation/**/*.py`、`conftest.py`、小体积 `test_cases/*.json`、评测脚本等非密钥文本 |
| **勿提交** | `tests/evaluation/runners/trajectories/`、`**/evaluation_results.json`、生成到 `docs/` 的报告或导出 JSON |

**轨迹 JSON、聚合评分与 HTML 报告**体积大且环境相关；对话片段中还可能误粘贴 **API Key**，即使已在 `.gitignore` 中列出，也**不要**使用 `git add -f` 强行入库。根目录 `.gitignore` 已忽略 `tests/evaluation/runners/trajectories/`、`tests/evaluation/**/evaluation_results.json`、`docs/EVALUATION_REPORT.html`、`docs/evaluation_results.json` 等。

---

## 4. 自我优化子系统

自我优化系统基于运行日志和代码分析生成优化提案，详见 [SELF_OPT.md](SELF_OPT.md)。

### 4.1 运行日志驱动提案

通过 Trace 系统采集运行指标，识别性能瓶颈、高频错误、异常行为：

- **慢工具识别**：平均时延超过阈值（`min_duration_ms_threshold: 2000`）
- **失败率统计**：成功率低于阈值（`min_failure_rate_threshold: 0.05`）
- **错误聚合**：按类型/工具分组，标记用户误用 vs 工具缺陷
- **Token 消耗分析**：总 token > 100000 时生成优化提案

### 4.2 提案持久化

提案存储在 `workspaces/self_opt/proposals/`（或配置的 `proposal_output_dir`）：

- `proposals-{YYYY-MM-DD}.jsonl`：每日提案追加写入
- `reports/runtime-{YYYY-MM-DD}.json`：运行分析报告
- `reports/trace-report-{YYYY-MM-DD}.json`：Trace 统计报告

### 4.3 自动执行控制

- **默认仅生成提案**（`auto_apply: false`），需人工批准执行
- **开启自动执行**（`auto_apply: true`）时仅执行低风险提案
- **风险等级上限**（`auto_apply_max_risk: "low"`）可配置为 `medium` 或 `high`

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
└── get_trace_file()               # 获取当前 trace 文件路径

miniagent.infrastructure.trace_events
├── 事件类型常量（EVENT_LLM_REQUEST 等）
└── 事件构建函数（make_error_event 等）

miniagent.infrastructure.trace_stats
├── load_trace_events()            # 加载事件
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
| `EVENT_SESSION_START` | `session.start` | 会话开始 |
| `EVENT_SESSION_END` | `session.end` | 会话结束 |
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

配置 `config.defaults.json`：

```json
{
  "trace": {
    "enabled": true,
    "output_dir": "workspaces/logs",
    "include_memory_ops": true,
    "include_context_ops": true,
    "retention_days": 7
  }
}
```

或使用环境变量：

```bash
MINIAGENT_TRACE_LOG_FILE=workspaces/logs/trace.jsonl
```

文件命名：`trace-{YYYY-MM-DD}.jsonl`（每日一个文件）。

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
    "total_tokens": {"prompt": 5000, "completion": 2000},
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

# 删除超过 7 天的 trace 文件
deleted = cleanup_old_traces(retention_days=7)
```

### 6.2 提案文件清理

```python
from miniagent.core.self_opt.proposal_store import ProposalStore

# 删除超过 30 天的提案文件
deleted = ProposalStore.cleanup_old_proposals(retention_days=30)
```

---

## 7. 相关文档

- [SELF_OPT.md](SELF_OPT.md) — 自我优化系统详解
- [CLI.md](CLI.md) — CLI 命令手册（自我优化命令）
- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构
- [CONTRIBUTING.md](CONTRIBUTING.md) — 代码规范
- [PERFORMANCE.md](PERFORMANCE.md) — 性能分析流程