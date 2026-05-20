# 可选：本地离线测评（Offline Evaluation）

> Mini Agent Python | 版本与行为以 `miniagent.__version__` 及主文档为准；本页描述**可选**开发者自测流程。

仓库中的 **`tests/evaluation/`** 用于离线轨迹录制、工具选择准确率、对抗用例与可选 LLM 评判等实验。**评测源码（`.py`、`conftest`、小体积 `test_cases/*.json`）应纳入 Git**，与主测试树一并追踪；本节说明如何用 pytest/Git 约束配合该目录。

---

## 1. 与主测试套件的关系

- **默认 CI / 本地对齐门禁**：`python -m pytest tests/ -q -m "not evaluation"`（见 [ENGINEERING.md](ENGINEERING.md) 第 2 节）。会排除 `tests/evaluation/` 下全部用例（该目录内测试由 `conftest` 统一打上 `evaluation` marker），避免未配置 Key 时失败或拖慢矩阵。
- **跑全量（含评测）**：`python -m pytest tests/ -q`。
- **仅评测**：`python -m pytest tests/ -m evaluation -v --tb=short`。
- **云端可选**：在 GitHub 上对同一 workflow 使用 **手动 `workflow_dispatch`** 可触发仅跑 `evaluation` marker 的 job（可在仓库 Secrets 中配置 `OPENAI_API_KEY`、`TAVILY_API_KEY`）。

---

## 2. 环境准备

```bash
pip install -e ".[dev,typing]"
```

- 完整本地门禁见 [ENGINEERING.md](ENGINEERING.md) §2。
- 涉及 **LLM 评判**（如 `evaluators/llm_judge.py`）时，需在 `.env` 中配置与主 Agent 相同的 **`OPENAI_API_KEY`**（或其它评测脚本读取的模型环境变量，以脚本内说明为准）。
- 涉及 **飞书 / 浏览器 / MCP** 的用例时，按需安装：`pip install -e ".[dev,feishu]"`、`.[browser]`、`.[mcp]`。

---

## 3. 运行方式（按仓库实际脚本为准）

评测代码布局可能包含：

| 路径（示例） | 用途 |
|--------------|------|
| `tests/evaluation/run_phase4.py` | 聚合或分阶段跑评测流水线 |
| `tests/evaluation/runners/offline_eval.py` | 离线回放 / 轨迹评估 |
| `tests/evaluation/test_phase*.py` | pytest 收集的阶段性集成测试 |
| `tests/evaluation/test_cases/*.json` | 用例定义（体积通常较小，可入库） |

**示例**（存在对应文件时执行）：

```bash
# 仅跑评测子目录下的 pytest 用例
python -m pytest tests/evaluation/ -v --tb=short

# 若仓库提供独立入口脚本（名称以实际文件为准）
python tests/evaluation/run_phase4.py
```

生成 **HTML / Markdown 报告** 时，若仓库内含 `reporters/generate_report.py`，按其 `--help` 指定输出路径；建议输出到 **`MINI_AGENT_STATE`** 下的临时目录或本机 `$TMP`，避免写入仓库根。

---

## 4. 产物目录约定（请勿提交）

下列路径已在根目录 [`.gitignore`](../.gitignore) 中忽略；**请勿 `git add -f`**：

- **`tests/evaluation/runners/trajectories/`** — 录制的轨迹 JSON（体积大、环境相关；**内容可能含用户对话中粘贴的密钥**，泄漏风险高于源码）。
- **`tests/evaluation/**/evaluation_results.json`** — 聚合评分结果。
- **`docs/EVALUATION_REPORT.html`**、`docs/evaluation_results.json` — 生成报告或导出结果（若你选择写到 `docs/`）。

需要留存结论时，请将**脱敏后的摘要**写入 issue、PR 描述或团队 wiki，而不是把原始轨迹入库。

---

## 5. 状态目录与并行运行

与主程序一致，建议在跑长时间评测前设置 **`MINI_AGENT_STATE`** 指向临时目录（见 [ENGINEERING.md](ENGINEERING.md) 第 3 节、[USER_GUIDE.md](USER_GUIDE.md)），避免与日常 `workspaces/` 会话互相干扰。

---

## 6. 相关文档

- [ENGINEERING.md](ENGINEERING.md) — 质量门禁、Git 与状态目录政策。
- [INDEX.md](INDEX.md) — 文档索引。
- [ARCHITECTURE.md](ARCHITECTURE.md) — 被测系统架构说明。
