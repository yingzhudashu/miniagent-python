# 贡献指南

> Mini Agent Python | 版本: 2.0.2

## 项目架构

项目按一级子包划分职责（共 12 个：`cli`、`core`、`engine`、`feishu`、`infrastructure`、`memory`、`session`、`skills`、`tools`、`security`、`types`、`runtime`）：

| 子包 | 职责 | 关键文件 |
|------|------|---------|
| `cli/` | 控制台入口脚本（`project.scripts` → `main`） | cli.py |
| `core/` | Agent 核心逻辑（规划+执行） | agent.py, executor.py, planner.py, openai_client.py |
| `engine/` | 运行时编排、CLI、生命周期 | main.py, engine.py, command_dispatch.py |
| `feishu/` | 飞书通信 | poll_server.py, agent_handler.py |
| `infrastructure/` | 基础设施（注册表、监控、队列） | registry.py, message_queue.py, instance.py |
| `memory/` | 三层记忆系统 | store.py, context.py, keyword_index.py, defaults.py |
| `session/` | 会话管理与持久化 | manager.py, workspace.py |
| `skills/` | 可插拔技能系统 | registry.py, loader.py, clawhub_client.py |
| `tools/` | LLM 可调用的工具 | exec.py, filesystem.py, web.py |
| `security/` | 沙箱与权限 | sandbox.py |
| `types/` | 共享类型定义 | agent.py, config.py, tool.py, planning.py |
| `runtime/` | 进程级组合根 | `context.py`（`RuntimeContext`） |

**版本号**：以 `miniagent.__version__`（`miniagent/__init__.py`）为权威；`pyproject.toml` 通过 `tool.setuptools.dynamic` 读取该属性。

## 开发环境设置

```bash
# 1. 克隆项目（将 <repo-url> 换为你的 fork 或上游 Git 远程；README 快速开始中亦使用同一占位）
git clone <repo-url>
cd miniagent-python

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate     # Windows
source .venv/bin/activate  # Linux/Mac

# 3. 安装依赖（开发模式）
pip install -e ".[dev]"

# 4. 配置环境
cp .env.example .env
# 编辑 .env 填入 API Key

# 5. 运行测试（与 CI 默认一致：排除 evaluation marker）
python -m pytest tests/ -q -m "not evaluation"
# 含 tests/evaluation 全量：
# python -m pytest tests/ -q
```

## 运行时目录与测试隔离

本地开发时，Agent 默认把状态写在仓库下的 `workspaces/`（实例心跳、会话历史、锁文件等）。**默认不应把这些当作必须提交的源码**：仓库根 `.gitignore` 已忽略 `workspaces/instances/`、锁文件、`workspaces/sessions/` 等路径；若团队需要提交示例配置，可个案取消跟踪。

自动化测试与 CI 推荐设置 **`MINI_AGENT_STATE`** 指向临时目录（与 `tests/test_startup.py` 等一致），避免测试污染本机 `workspaces/` 或与并行运行冲突，例如：

```bash
# PowerShell 示例（单次会话）
$env:MINI_AGENT_STATE = "$env:TEMP\miniagent-test-state"
python -m pytest tests/ -q -m "not evaluation"
```

### 推送前自检（密钥与轨迹）

- **勿提交** `.env`、含真实 Key 的 JSON、评测轨迹目录（见 [EVALUATION_LOCAL.md](EVALUATION_LOCAL.md)）；即使 `.gitignore` 已排除，也不要对可疑路径使用 `git add -f`。
- 推送前执行 `git diff --cached`，确认无意加入密钥或完整对话导出。
- 可选：在仓库根执行检索，排查误粘贴（示例：`tvly-` 前缀、`sk-` 形态的长串需与文档占位符区分）。GitHub 侧建议开启 Secret scanning / Push protection。

## 编码规范

### 基本规则

| 规则 | 要求 |
|------|------|
| 编码 | UTF-8，无 BOM |
| 行长度 | 最大 100 字符（与 Ruff 一致） |
| 缩进 | 4 空格 |
| 引号 | 双引号优先 |
| 注释语言 | 中文 |
| docstring | 必须，中文 |

### 类型注解

所有公开函数必须有类型注解：

```python
# ✅ 好
def resolve_session(manager: SessionManager, id_or_number: str) -> str | None:
    """解析会话标识。"""
    ...

# ❌ 差
def resolve_session(manager, id_or_number):
    ...
```

### 模块级 docstring

每个 `.py` 文件必须有模块级 docstring：

```python
"""会话管理器

管理多会话的创建、切换、重命名和持久化。
支持内存和磁盘双重查找，编号和 ID 双重解析。

依赖:
- miniagent.session.workspace: 工作空间管理
- miniagent.engine.session_lock: 会话锁
"""
```

### 导入顺序

```python
# 1. 标准库
from __future__ import annotations
import os
import sys

# 2. 第三方库
from openai import AsyncOpenAI

# 3. 项目内部
from miniagent.types.config import AgentConfig
from miniagent.infrastructure.logger import get_logger
```

### 日志 vs print

```python
# ✅ 使用 logger（非 CLI 模块）
from miniagent.infrastructure.logger import get_logger
_logger = get_logger(__name__)
_logger.info("飞书连接已建立")

# ❌ 使用 print（仅允许在 CLI 交互模块中）
print("飞书连接已建立")
```

## 测试

### 运行测试

```bash
# 全部测试
python -m pytest tests/ -v

# 快速模式
python -m pytest tests/ -x -q

# 单个模块
python -m pytest tests/test_session.py -v

# 匹配名称
python -m pytest tests/ -k "test_register"
```

### 测试文件命名

| 源文件 | 测试文件 |
|--------|---------|
| `miniagent/infrastructure/registry.py` | `tests/test_registry.py` |
| `miniagent/session/manager.py` | `tests/test_session.py` |
| `miniagent/security/sandbox.py` | `tests/test_sandbox.py` |

### 添加新测试

```python
"""测试新功能模块"""

import pytest
from miniagent.your_module import YourClass

class TestYourClass:
    """YourClass 单元测试。"""

    def test_basic_operation(self):
        """测试基本操作。"""
        obj = YourClass()
        assert obj.do_something() == expected

    @pytest.mark.asyncio
    async def test_async_operation(self):
        """测试异步操作。"""
        result = await obj.async_method()
        assert result is not None
```

### 测试覆盖率目标

- 核心模块 (`core/`, `infrastructure/`): > 80%
- 工具模块 (`tools/`): > 60%
- 集成测试: 覆盖主要工作流

## 添加新功能

### 添加新工具

1. 在 `miniagent/tools/` 创建新文件
2. 实现 `register_<name>_tools(registry)` 函数
3. 在 `miniagent/engine/init.py` 注册
4. 添加测试

```python
"""新工具模块"""
from miniagent.types.tool import ToolResult, ToolContext

async def my_tool_handler(args: dict, ctx: ToolContext) -> ToolResult:
    """工具处理函数。"""
    # 实现逻辑
    return ToolResult(success=True, content="结果")
```

### 添加新技能

1. 在 `workspaces/skills/` 创建技能目录
2. 编写 `manifest.yaml`
3. 实现技能模块
4. 技能会被 `miniagent/skills/loader.py` 自动发现

### 添加新 CLI 命令

1. 在 `miniagent/engine/cli_commands.py` 添加 `cmd_<name>()` 函数
2. 在 `miniagent/engine/command_dispatch.py` 注册路由
3. 在 `cmd_help()` 添加帮助文本
4. CLI 和飞书自动共享新命令

## 注释与文档（分层约定）

| 层级 | 要求 |
|------|------|
| 模块 | 文件顶部 docstring：职责、主要依赖、async/线程假设 |
| 公开 API | 类与公开函数：中文 docstring，说明参数、副作用、可能异常 |
| 复杂分支 | 如 CLI 主循环、命令调度、ReAct、多实例：关键分支旁简短说明「为何如此」 |
| 避免 | 逐行复述代码；大段迁移史放在 CHANGELOG / ARCHITECTURE |

## Git 规范

### 分支策略

| 分支 | 用途 |
|------|------|
| `main` | 稳定版本 |
| `dev` | 开发分支 |
| `feature/<name>` | 功能分支 |
| `fix/<name>` | 修复分支 |

### 提交消息

```
<type>: <简要描述>

<详细说明（可选）>
```

类型: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

示例:
```
feat: 添加 .status 命令支持飞书和 CLI
fix: 修复 session_manager 磁盘会话解析
docs: 补全三层记忆系统文档
refactor: 拆分 unified.py 为 engine/ 包
```

## 发布流程

1. 更新 `CHANGELOG.md`
2. 更新 `miniagent/__init__.py` 中的 `__version__`
3. 运行全量测试 `python -m pytest tests/ -v`
4. 提交并打 tag

## 软件工程实践（仓库卫生）

更完整的清单（质量门禁、单一事实来源、文档对齐）见 **[ENGINEERING.md](ENGINEERING.md)**。

| 项目 | 约定 |
|------|------|
| **单一可安装包** | 开发与安装均以 ``miniagent`` 包为准；不再维护顶层 ``src`` 兼容包或根目录 ``requirements.txt``。依赖声明只在 ``pyproject.toml``。 |
| **CI** | [``.github/workflows/ci.yml``](../.github/workflows/ci.yml) 在 push/PR 上对 Python 3.10 / 3.12 运行 ``compileall``、``ruff check miniagent tests`` 与 ``pytest``；合并前应在本地执行相同命令。 |
| **状态目录** | 默认 ``workspaces/``；测试与并行运行请设置 ``MINI_AGENT_STATE``，避免污染本机数据（见上文「运行时目录与测试隔离」与根目录 ``.env.example`` 注释）。 |
| **忽略规则** | ``.gitignore`` 已排除 ``__pycache__``、``.pytest_cache``、``.ruff_cache``、``*.egg-info``、本地 ``debug-*.log`` 及常见运行时产物；勿将含密钥的 ``.env`` 提交入库。 |

### 文档与版本对齐清单（发版或大范围文档改动时）

1. ``miniagent/__init__.py`` 中 ``__version__`` 已与 ``CHANGELOG``、主要 ``docs/*.md`` 顶部版本标语一致。
2. ``docs/INDEX.md`` 中目录树与实际仓库一致（已移除的文件勿再列出）。
3. 涉及行为变更时同步 ``ARCHITECTURE.md`` / 专题文档（如 ``INSTANCE_REGISTRY.md``）。
4. ``README.md`` 中的命令、测试数量与 CI 保持可验证（测试数以 ``pytest --collect-only`` 为准）。
