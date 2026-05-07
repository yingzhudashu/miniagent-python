# Mini Agent Python

> Mini Agent 的 Python 实现 — 一个基于 LLM 的智能个人助手，支持工具调用、技能系统、自我优化。

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-73%20passed-brightgreen.svg)](tests/)

---

## 特性

- **两阶段架构**: Plan-then-Execute（规划 → 执行）
- **Agent 身份系统**: 内置 `MiniAgent` 身份认知，规划器和执行器有独立的 System Prompt
- **工具系统**: 文件系统、命令执行、网页搜索、技能管理、自我优化
- **技能系统**: 可插拔技能包，支持 ClawHub 远程搜索和安装
- **自我优化**: 自动审视、提案生成、测试验证、Git 回滚
- **飞书集成**: WebSocket 长轮询 + Webhook HTTP 服务器，支持 WebSocket 消息回调
- **CLI 交互**: 彩色终端输出、去重显示、实时进度指示、内置命令管理
- **多会话**: 每个聊天/会话独立上下文和工具
- **子进程管理**: 自动跟踪和清理孤儿进程

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
│   │   ├── planner.py     # LLM 规划器（含 Agent 身份定义）
│   │   ├── executor.py    # ReAct 执行器（含 Agent 身份定义）
│   │   ├── registry.py    # 工具注册表
│   │   ├── monitor.py     # 性能监控
│   │   ├── memory_store.py # 三层记忆
│   │   ├── process_tracker.py # 子进程跟踪与清理
│   │   ├── self_opt/      # 自我优化子系统
│   │   └── ...
│   ├── tools/             # 内置工具
│   │   ├── filesystem.py  # 文件操作
│   │   ├── exec.py        # 命令执行（含进程跟踪）
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
│   │   ├── cli.py         # 主循环
│   │   └── display_manager.py # 终端显示管理
│   ├── session/           # 会话管理
│   └── security/          # 安全沙箱
├── tests/                 # pytest 测试
└── skills/                # 技能包目录
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `.stats` | 工具使用统计 |
| `.skills` | 已加载技能列表 |
| `.sessions` | 会话列表 |
| `.session new/switch/destroy` | 会话管理 |
| `.profile [name]` | 查看/切换模型预设 |
| `.skill search/install/list` | 技能管理 |
| `.plan <内容>` | 跳过规划直接执行 |
| `.log <路径>` | 开启增量日志 |
| `.optimize inspect/status` | 自我优化 |
| `.promote/demote` | 工具升降维 |
| `.help` | 帮助 |
| `quit` / `exit` | 退出 |

## 测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_sandbox.py
pytest tests/test_integration.py -v
```

## 许可证

MIT

## 开发指南

### 日志系统

项目提供两种日志能力：

```python
# 1. 控制台日志（替代 print）
from src.core.logger import get_logger
logger = get_logger(__name__)
logger.info("模块已加载")

# 2. 结构化文件日志（JSONL）
from src.core.logger import append_log
append_log("agent.jsonl", {"phase": "exec", "turn": 1})
```

### 代码规范

- **类型标注**: 所有公开函数必须有完整类型提示
- **Docstring**: 模块/类/函数都需要 Google-style docstring
- **print → logging**: 库代码使用 `get_logger()`，CLI 入口可用 `print()`
- **错误处理**: 禁止 bare except，捕获具体异常类型
- **测试**: 新增功能需配套测试，保持 73+ 测试全通过
