# Mini Agent Python

![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-2.1.0-blue)
![Tests](https://img.shields.io/badge/tests-dynamic-blue)
> **测试数量**：以 `pytest --collect-only -q` 为准，不硬编码以避免漂移（见 [CONTRIBUTING.md](docs/CONTRIBUTING.md) §文档与版本对齐清单）
![Coverage](https://img.shields.io/badge/coverage-85%25%20整体-yellow)

基于 LLM 的多阶段智能代理系统。支持 CLI 和飞书双通道接入。

## 特性

- **多阶段智能**：分类 → 需求澄清 → 规划 → ReAct 执行
- **三层记忆** + **双通道接入**（CLI + 可选飞书 WebSocket）
- **定时任务**、**多实例**、**可插拔技能**（ClawHub）、**自我优化**、**沙箱安全**

完整功能清单见 [docs/INDEX.md](docs/INDEX.md) §功能清单。架构与配置细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)、[docs/FEISHU.md](docs/FEISHU.md)、[docs/ENGINEERING.md](docs/ENGINEERING.md) §3.3。

## 快速开始

```bash
git clone <repo-url> && cd miniagent-python
pip install -e ".[dev,typing]"    # 开发安装；仅需运行时：pip install -e .
cp config.defaults.json config.user.json   # 编辑填入 secrets.openai_api_key
python -m miniagent
```

详细安装、可选 extra（`feishu` / `cli` / `browser` / `mcp`）、技能与联网配置见 **[docs/USER_GUIDE.md](docs/USER_GUIDE.md) §3–5**。本地质量门禁见 [docs/ENGINEERING.md](docs/ENGINEERING.md) §2。

## 常用命令

| 命令 | 说明 |
|------|------|
| `/status` | 检查 Agent 状态 |
| `/session list` | 列出会话 |
| `/help` | 完整帮助 |
| `/schedule` | 定时任务（CLI 完整管理） |
| `/btw start <prompt>` | 后台并行任务 |

完整点命令手册见 **[docs/CLI.md](docs/CLI.md)**。

## 文档

**新手请先看** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。完整索引、按角色导航与 SSOT 对照见 **[docs/INDEX.md](docs/INDEX.md)**。

## 测试

```bash
python -m pytest tests/ -q -m "not evaluation"   # 与默认 CI 一致（排除 tests/evaluation 下 marker）
python -m pytest tests/ -q                       # 含评测子目录全部用例
python -m ruff check miniagent tests
python -m compileall -q miniagent
python -m mypy miniagent/types                   # 与默认 CI `test` job 一致（需 pip install -e ".[dev,typing]"）
```

用例数量以 `pytest tests/ --collect-only -q` 的收集结果为准（勿在文档中硬编码条数以免漂移）；与 [CONTRIBUTING.md](docs/CONTRIBUTING.md) §文档与版本对齐清单一致。

评测目录说明与产物勿提交约定见 [ENGINEERING.md](docs/ENGINEERING.md) §3.2。

## 技术栈

- Python 3.10+
- OpenAI API (GPT-4o-mini)
- 飞书 SDK (lark-oapi, WebSocket)
- pytest (单元测试)

**可选 pip extra**（与 [`pyproject.toml`](pyproject.toml) 一致；权威说明见 [docs/ENGINEERING.md](docs/ENGINEERING.md) 第 1 节）：`dev`（pytest / ruff / pytest-cov）、`typing`（mypy）、`cli`（Rich）、`feishu`（lark-oapi）、`browser`（playwright）、`mcp`（官方 mcp SDK）。

## License

MIT
