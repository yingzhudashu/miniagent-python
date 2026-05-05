# Mini Agent Python

> Mini Agent 的 Python 实现 — 一个基于 LLM 的智能个人助手，支持工具调用、技能系统、自我优化。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 特性

- **两阶段架构**: Plan-then-Execute（规划 → 执行）
- **工具系统**: 文件系统、命令执行、网页搜索、技能管理
- **技能系统**: 可插拔技能包，支持 ClawHub 集成
- **自我优化**: 自动审视、提案生成、测试验证、Git 回滚
- **飞书集成**: WebSocket 长轮询 + Webhook HTTP 服务器
- **CLI 交互**: 内置命令管理 (.stats, .skills, .sessions, .optimize)
- **多会话**: 每个聊天/会话独立上下文和工具

## 快速开始

### 安装

```bash
pip install -e .
```

### 配置

```bash
cp .env.example .env
# 编辑 .env 填写你的配置
```

关键环境变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | `sk-xxx` |
| `OPENAI_BASE_URL` | API 端点（可选） | `https://api.openai.com/v1` |
| `MODEL` | 使用的模型 | `gpt-4o-mini` |
| `FEISHU_APP_ID` | 飞书应用 ID | `cli_xxx` |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | `xxx` |

### 运行

```bash
# CLI 交互模式
python -m src

# 飞书长轮询模式
python -m src --feishu

# 强制启动
python -m src --force

# 停止运行中的实例
python -m src --stop
```

## 项目结构

```
miniagent-python/
├── src/
│   ├── core/              # 核心引擎
│   │   ├── agent.py       # Agent 编排层
│   │   ├── planner.py     # LLM 规划器
│   │   ├── executor.py    # ReAct 执行器
│   │   ├── registry.py    # 工具注册表
│   │   ├── monitor.py     # 性能监控
│   │   ├── memory_store.py # 三层记忆
│   │   ├── self_opt/      # 自我优化子系统
│   │   └── ...
│   ├── tools/             # 内置工具
│   │   ├── filesystem.py  # 文件操作
│   │   ├── exec.py        # 命令执行
│   │   ├── web.py         # 网页工具
│   │   └── self_opt.py    # 自我优化工具
│   ├── skills/            # 技能系统
│   │   ├── registry.py    # 技能注册
│   │   ├── loader.py      # 技能加载
│   │   └── clawhub_client.py
│   ├── feishu/            # 飞书集成
│   │   ├── poll_server.py # WebSocket 长轮询
│   │   ├── server.py      # Webhook 服务器
│   │   └── agent_handler.py
│   ├── cli/               # CLI 入口
│   │   └── cli.py
│   ├── session/           # 会话管理
│   └── security/          # 安全沙箱
├── tests/                 # pytest 测试
└── skills/                # 技能包目录
```

## 测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_sandbox.py
pytest tests/test_integration.py -v
```

## 迁移状态

本项目是从 TypeScript 版本的 [mini-agent](https://github.com/yingzhudashu/mini-agent) 迁移而来。

| 阶段 | 状态 | 文件数 |
|------|------|--------|
| 1. 项目骨架+类型 | ✅ | ~10 |
| 2. 基础设施 | ✅ | ~7 |
| 3. 会话+记忆 | ✅ | ~5 |
| 4. 核心引擎 | ✅ | 3 |
| 5. 工具实现 | ✅ | 5 |
| 6. 技能系统 | ✅ | 3 |
| 7. CLI 入口 | ✅ | 2 |
| 8. 飞书集成 | ✅ | 4 |
| 9. 自我优化 | ✅ | 15 |
| 10. 测试+文档 | ✅ | ~10 |

## 许可证

MIT
