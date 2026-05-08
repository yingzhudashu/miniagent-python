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
- **会话持久化**: 对话历史自动保存到磁盘，重启后恢复
- **统一模式**: CLI + 飞书单进程运行，共享会话和工具
- **桥接模式**: CLI 和飞书独立进程间通过 HTTP 桥接共享会话

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

# 统一模式（CLI + 飞书单进程）
python -m src --unified

# 统一模式（仅飞书）
python -m src --unified --feishu

# 强制启动
python -m src --force

# 停止运行中的实例
python -m src --stop
```

## 运行模式

Mini Agent 支持三种运行模式，根据部署场景选择：

### 1. CLI 模式（默认）

```bash
python -m src
```

- 单进程，终端交互
- 适合开发调试、本地使用
- 会话历史自动持久化到 `state/workspaces/`

### 2. 飞书模式

```bash
python -m src --feishu
```

- 单进程，WebSocket 长轮询接收飞书消息
- 内置轻量 HTTP 桥接服务器（端口 18789）
- CLI 可通过桥接注入消息到飞书会话
- 适合部署为飞书机器人

### 3. 统一模式（推荐）

```bash
python -m src --unified        # CLI + 飞书同时运行
python -m src --unified --feishu  # 仅飞书
```

- 单进程同时运行 CLI 和飞书服务器
- 共享 `registry`、`monitor`、`session_manager`
- **零桥接开销**：CLI 和飞书天然同步
- 飞书消息的思考过程实时显示在 CLI 终端
- CLI 可通过 `.send` 命令注入消息到任意飞书会话

## 会话持久化

### 机制

每个会话的对话历史自动保存到磁盘：

```
state/workspaces/<safe_session_id>/history.json
```

格式：
```json
[
  {"role": "user", "content": "你好"},
  {"role": "assistant", "content": "你好！有什么可以帮你的？"}
]
```

### 行为

- **自动保存**：每次 agent 回复后自动写入磁盘
- **自动加载**：进程重启时从磁盘恢复历史
- **长度限制**：最多保留 40 条消息（约 20 轮对话）
- **静默失败**：持久化失败不影响主流程

### 桥接模式

当 CLI 和飞书运行在不同进程时，通过 HTTP 桥接共享会话状态：

```
CLI 进程 <----HTTP 18789----> 飞书进程
```

CLI 端通过 `--standalone` 标志启用桥接：
```bash
python -m src.cli.cli --standalone
```

桥接支持的操作：
| 操作 | 说明 |
|------|------|
| `status` | 获取活跃会话列表 |
| `inject` | 注入用户消息到飞书会话 |
| `inject_reply` | 同步 CLI 回复到飞书会话 |
| `get_history` | 获取会话历史 |

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

### 通用命令

| 命令 | 说明 |
|------|------|
| `.stats` | 工具使用统计 |
| `.skills` | 已加载技能列表 |
| `.sessions` | 会话列表（含飞书会话） |
| `.session new/switch/destroy` | 会话管理 |
| `.profile [name]` | 查看/切换模型预设 |
| `.skill search/install/list` | 技能管理 |
| `.plan <内容>` | 跳过规划直接执行 |
| `.log <路径>` | 开启增量日志 |
| `.optimize inspect/status` | 自我优化 |
| `.promote/demote` | 工具升降维 |
| `.help` | 帮助 |
| `quit` / `exit` | 退出 |

### 统一模式专属命令

| 命令 | 说明 |
|------|------|
| `.send <session_id> <message>` | 向指定会话注入消息 |
| `.stop` | 停止实例并退出 |

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

## 架构

### 核心组件关系

```
┌─────────────────────────────────────────────────┐
│                   入口层                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │   CLI    │  │  飞书    │  │   Unified     │  │
│  │  cli.py  │  │  poll_   │  │  unified.py   │  │
│  │          │  │  server  │  │               │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │             │                │           │
│       │        ┌────┴─────┐          │           │
│       │        │ agent_   │          │           │
│       │        │ handler  │          │           │
│       │        └────┬─────┘          │           │
└───────┼─────────────┼────────────────┼───────────┘
        │             │                │
        ▼             ▼                ▼
┌─────────────────────────────────────────────────┐
│                   引擎层                          │
│                                                 │
│  ┌─────────────────────────────────────────────┐│
│  │              run_agent()                     ││
│  │  ┌──────────┐    ┌────────────────────────┐ ││
│  │  │ planner  │───>│ executor (ReAct loop)  │ ││
│  │  │ (规划)   │    │ (工具调用 + 推理)       │ ││
│  │  └──────────┘    └────────────────────────┘ ││
│  └─────────────────────────────────────────────┘│
│                                                 │
│  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ registry     │  │ session_manager          │ │
│  │ (工具注册)   │  │ (会话历史 + 持久化)       │ │
│  └──────────────┘  └──────────────────────────┘ │
│                                                 │
│  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ monitor      │  │ skill_registry           │ │
│  │ (统计监控)   │  │ (技能管理)               │ │
│  └──────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

### 数据流

```
用户输入
   │
   ▼
┌─────────────────┐
│ 消息去重         │ ← 内存 + 磁盘双重去重
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 顺序队列         │ ← 每个聊天室一个队列
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 会话历史加载     │ ← 从磁盘或内存
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ run_agent()     │
│  ├─ planner     │ ← LLM 生成执行计划
│  └─ executor    │ ← 执行工具调用
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 更新历史 + 持久化│ ← 写入 history.json
└────────┬────────┘
         │
         ▼
     回复用户
```

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
