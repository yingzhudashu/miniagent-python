# 软件工程实践与仓库卫生

> Mini Agent Python | 版本: 2.0.1 | 与 `miniagent.__version__` 对齐

本文档汇总本仓库在**可维护性、可重复构建、安全与协作**上的约定，作为 [CONTRIBUTING.md](CONTRIBUTING.md) 的补充：后者偏「如何写代码」，本文偏「仓库与发布如何保持健康」。

---

## 1. 单一事实来源（Single Source of Truth）

| 主题 | 权威位置 | 说明 |
|------|----------|------|
| 可安装包名与源码布局 | `pyproject.toml` → `[tool.setuptools.packages.find]` | 仅打包 `miniagent*`；不再维护顶层 `src` 作为可导入包。 |
| 版本号 | `miniagent/__init__.py` 中 `__version__` | `pyproject.toml` 通过 `dynamic.version` 读取；发版时与 `CHANGELOG.md`、本文档顶部标语一并更新。 |
| 依赖声明 | `pyproject.toml` `[project]` / `optional-dependencies` | 不使用根目录 `requirements.txt`；运行时依赖与可选组（`dev`、`feishu`）集中在此。 |
| 环境变量说明 | 仓库根 `.env.example` | 复制为 `.env` 后本地填写；**勿将含真实密钥的 `.env` 提交入库**（见 `.gitignore`）。 |
| 架构与行为细节 | `docs/ARCHITECTURE.md` 及各专题文档 | README 只做索引与快速上手；深度说明以 `docs/` 为准。 |

---

## 2. 质量门禁（本地与 CI）

合并或发版前建议至少通过以下检查（与 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 一致）：

```bash
python -m pip install -e ".[dev]"
python -m ruff check miniagent tests
python -m compileall -q miniagent
python -m pytest tests/ -q
```

CI 说明：

- **`test` job**（矩阵 Python 3.10 / 3.12）：`pip install -e ".[dev]"`，跑 `compileall`、`ruff`、`pytest`。
- **`test-feishu-extra` job**（仅 3.12）：`pip install -e ".[dev,feishu]"` 后再跑 `compileall`、`ruff` 与 `pytest`，确保安装 `lark-oapi` 时仍通过（与主矩阵并行，不拖慢双版本安装）。

说明：

- **Ruff**：风格与部分静态问题；配置见 `pyproject.toml` `[tool.ruff]`。
- **compileall**：全包语法编译，可捕获部分「仅某测试未覆盖路径」的语法错误。
- **Pytest**：默认 `asyncio_mode = auto`；未装 `lark-oapi` 时部分飞书路径可能跳过；本地可改用 `pip install -e ".[dev,feishu]"` 与 CI 飞书 job 对齐。

可选增强（未默认纳入 CI，团队可自行约定）：

- 覆盖率：`pytest --cov=miniagent --cov-report=term-missing`（需在 `optional-dependencies.dev` 中增加 `pytest-cov`）。

---

## 3. 状态目录与测试隔离

- **默认**：Agent 将实例心跳、会话、锁等写入仓库下 `workspaces/`（部分路径见 `.gitignore`，如 `workspaces/sessions/`、`**/*.lock`）。
- **推荐**：开发与 CI 设置 **`MINI_AGENT_STATE`** 指向临时目录，避免污染本机数据或与并行运行冲突（示例见 `CONTRIBUTING.md` 与 `.env.example` 注释）。
- **语义**：多实例注册、PID 判定与清理规则见 [INSTANCE_REGISTRY.md](INSTANCE_REGISTRY.md)。

### 3.1 `workspaces/` 与 Git 跟踪政策

**运行时生成物默认不入库**：`.gitignore` 已排除 `workspaces/instances/`、`workspaces/sessions/`、`**/*.lock`、`workspaces/cli/` 等，避免把本机 PID、会话历史提交到远程。

**当前仓库中仍被跟踪的少数路径**（视为「示例 / 文档化数据结构」，便于新人理解磁盘布局；**非**生产密钥）：

- `workspaces/keyword-index.json`
- `workspaces/memory/*.json`、部分 `workspaces/memory/*.md`

若 fork 后希望 **零跟踪** 任何运行时产物：在确认无团队依赖后，可 `git rm --cached` 上述路径、将对应 glob 写入 `.gitignore`，并更新本段说明。日常开发仍建议使用 `MINI_AGENT_STATE` 将状态迁出仓库。

---

## 4. 安全与密钥

- 密钥仅通过环境变量或本地 `.env` 注入；代码库中不出现真实 token。
- 工具执行与文件访问受 [SECURITY.md](SECURITY.md) 所述沙箱与策略约束；部署面见 [DEPLOYMENT.md](DEPLOYMENT.md)。

---

## 5. 文档维护清单

大范围重构或发版时建议核对：

1. `miniagent/__init__.py` 的 `__version__` 与 `CHANGELOG.md`、`docs/*` 顶部版本标语一致。
2. [INDEX.md](INDEX.md) 中目录树与仓库实际文件一致（含 `core/openai_client.py`、`memory/defaults.py` 等）。
3. README 中的命令、测试数量与 `pytest --collect-only -q` 输出一致。
4. 行为变更同步 `ARCHITECTURE.md` 或对应专题文档（如 `CHANNEL_BINDING.md`、`MEMORY_SYSTEM.md`）。

---

## 6. 相关链接

| 文档 | 用途 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、编码规范、测试约定 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 分层架构与数据流 |
| [INDEX.md](INDEX.md) | 全部文档索引 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本历史 |
