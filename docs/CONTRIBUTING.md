# 贡献指南

> Mini Agent Python | 版本: 2.1.0 | 最后更新: 2026-07-11 | 与 `miniagent.__version__` 对齐

本文档为开发者单一入口，分三部分：

- **Part 1 — 参与贡献**：环境、规范、测试、docstring、Git 与发布
- **Part 2 — 扩展开发**：工具 / 技能 / 通道扩展
- **Part 3 — API 编程示例**：`run_agent` 集成与 Mock 测试

提示词规范见 [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md)；仓库卫生与 CI 见 [ENGINEERING.md](ENGINEERING.md)。

---

## Part 1 — 参与贡献

## 项目架构

**17 个核心子包**（含可选 `mcp/`）的完整目录树见 **[README.md §项目结构](../README.md#项目结构)**。逻辑分层（12 功能层）见 [README.md §架构概览](../README.md#架构概览) 与 [ARCHITECTURE.md](ARCHITECTURE.md)。

**版本号**：以 `miniagent.__version__`（`miniagent/__init__.py`）为权威；`pyproject.toml` 通过 `tool.setuptools.dynamic` 读取该属性。

## 开发环境设置

克隆、虚拟环境与 `config.user.json` 的通用步骤见 **[README.md](../README.md) §安装** 与 **§配置**；Python 版本与可选 pip extra 见 **[DEPLOYMENT.md](DEPLOYMENT.md) §环境要求**。

```bash
pip install -e ".[dev,typing]"   # 与默认 CI test job 一致
cp config.defaults.json config.user.json   # 若尚未配置；编辑 secrets 部分
python -m pytest tests/ -q -m "not evaluation"
# 合并前完整本地门禁（ruff / compileall / mypy / pytest）见 [ENGINEERING.md](ENGINEERING.md) §2
```

## 运行时目录与测试隔离

本地开发时，Agent 默认把状态写在仓库下的 `workspaces/`（实例心跳、会话历史、锁文件等）。**默认不应把这些当作必须提交的源码**：仓库根 `.gitignore` 已忽略 `workspaces/instances/`、锁文件、`workspaces/sessions/`（canonical：`{paths.state_dir}/sessions/`，见 [ENGINEERING.md](ENGINEERING.md) §3）等路径；若团队需要提交示例配置，可个案取消跟踪。

自动化测试与 CI 使用 [`tests/config_helpers.py`](../tests/config_helpers.py) 的 `install_test_config` 写入隔离的 `config.user.json`（设置 `paths.state_dir` 等），避免测试污染本机 `workspaces/` 或与并行运行冲突。`tests/conftest.py` 已提供 `isolated_config_loader` fixture；新用例优先使用该方式，而非环境变量覆盖。

```python
from tests.config_helpers import install_test_config

def test_example(tmp_path):
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    ...
```

### 提交前仓库卫生（缓存与构建产物）

- 推送前执行 **`git status`**：索引与工作区中不应出现 **`__pycache__/`**、**`.pytest_cache/`**、**`.ruff_cache/`**、**`.mypy_cache/`**、**`*.egg-info/`** 等；这些路径已由根目录 [`.gitignore`](../.gitignore) 忽略，若仍出现在「待提交」列表中，说明曾用 **`git add -f`** 误加，应改为 `git rm --cached <路径>` 后仅提交源码。
- 仅清理**已被 Git 忽略**的本地生成物（不删除未跟踪的源码与新文件）时，可在仓库根执行：`git clean -fdX`（PowerShell / bash 相同）。**注意**：根目录 `.gitignore` 中的 **`config.user.json`** 也会被视作「已忽略文件」一并删除；执行前请备份密钥，或改用逐个删除缓存目录（如仅删 `**/__pycache__`）。**勿**使用 `git clean -fdx`（小写 `x` 会删除所有未跟踪文件，易误删未入库的新模块）。
- 与「运行时目录」一节配合：日常在 `config.user.json` 设置 **`paths.state_dir`** 指向仓库外目录，可减少 `workspaces/**/*.lock`、定时任务表等个人状态出现在 `git status` 中。

### 推送前自检（密钥与轨迹）

- **勿提交** `config.user.json`（含真实 Key）、含真实 Key 的 JSON、评测轨迹目录（相关内容勿入库，见 [docs/ENGINEERING.md](ENGINEERING.md) §3.2）；即使 `.gitignore` 已排除，也不要对可疑路径使用 `git add -f`。
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
| docstring | 必须，中文（每个 ``.py`` 须具备模块级 docstring；类与**非 magic** 函数须具备 docstring；见下「缺失项扫描」与 magic 例外） |

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

与 CI 一致的 pytest / 覆盖率命令见 **[INDEX.md §测试与质量](INDEX.md#测试与质量)**。以下为开发常用变体（`-x` 快速失败、单模块、`-k` 匹配）：

```bash
python -m pytest tests/ -x -q                              # 快速模式
python -m pytest tests/test_session.py -v                # 单个模块
python -m pytest tests/ -k "test_register"               # 匹配名称
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

- 核心模块 (`core/`, `infrastructure/`): ≥ 95%
- 整体包 (`miniagent/`): ≥ 80%
- 工具模块 (`tools/`): ≥ 60%
- 集成测试: 覆盖主要工作流

权威说明见 [TEST_COVERAGE_MATRIX.md](TEST_COVERAGE_MATRIX.md) 与 [INDEX.md](INDEX.md) §测试与质量。

## 文档字符串（docstring）规范

完整分层约定见下表；本节补充**写法模板**与**交叉引用**，与 [ARCHITECTURE.md](ARCHITECTURE.md) 及 [ENGINEERING.md](ENGINEERING.md) §1 一并维护。

### 语言与风格

- **语言**：模块、类、函数 docstring 使用**简体中文**（与代码内注释一致）。
- **交叉引用**：在模块级 docstring 中引用其它模块/类时，可与代码库现有写法一致，使用 rST 指令（如 ``:mod:``、``:class:``）或反引号包裹的完整限定名，便于 Sphinx 与 IDE 解析；与 [ARCHITECTURE.md](ARCHITECTURE.md) 术语保持一致。
- **详略**：简单 getter、单行委托函数用 **1～3 行**说明「做什么」即可；含 IO、并发、环境变量或跨子系统契约的函数应写清 **Args / Returns / Raises / Note**（按需选段，不必强行四段俱全）。

### 模块级 docstring 建议包含

1. **一句话职责**（本文件解决什么问题）。
2. **与架构的对应关系**（可写「详见 ARCHITECTURE.md §…」或链到具体子系统名，如消息队列、多阶段架构）。
3. **依赖与边界**：主要 import、是否仅主线程、是否假设已有 ``RuntimeContext``、是否读写 ``paths.state_dir`` 等。
4. **非显而易见的行为**：例如懒加载、与飞书/CLI 共用路径、默认环境变量开关。

### 函数级模板

**无参或薄封装：**

```python
def get_foo() -> Foo:
    """返回进程内缓存的 Foo 单例（首次调用时创建）。"""
```

**带参数与返回值：**

```python
def merge_config(base: AgentConfig, overrides: dict[str, Any]) -> AgentConfig:
    """将 ``overrides`` 中的已知键合并进 ``base`` 的副本。

    Args:
        base: 默认配置（不会被原地修改）。
        overrides: 扁平或嵌套补丁字典，未知键忽略。

    Returns:
        新的 ``AgentConfig`` 实例。

    Note:
        运行时配置以 JSON 为准（``config.user.json`` > ``config.defaults.json``），见 [ENGINEERING.md](ENGINEERING.md) §1.1。
    """
```

**异步与副作用（网络/磁盘）：**

```python
async def dispatch_message(queue: MessageQueue, item: QueueItem) -> None:
    """将 ``item`` 投递进队列；可能在抢占模式下取消同 chat 上正在执行的任务。

    Args:
        queue: 进程内消息队列。
        item: 待处理消息封装。

    Raises:
        RuntimeError: 队列已关闭时。

    Note:
        与 ``preemptive`` 模式相关的不变量见 ARCHITECTURE.md 消息队列章节。
    """
```

### 子包 ``__init__.py``

- 若导出公共符号，模块 docstring 中列出**子包职责**与**主要导出**（或写明「聚合导出，实现见子模块」），避免空文件无说明。

### 缺失项扫描（可选）

仓库提供 ``scripts/docstring_inventory.py``，可列出当前仍缺 docstring 的模块与符号；生成 Markdown 报告：

```bash
python scripts/docstring_inventory.py --write docs/docstring_inventory.md
```

**与上表「docstring 必须」的关系**：脚本为自动化清单——除 ``__init__`` 外，名称形如 ``__x__`` 的方法**不在清单中检查**（不要求 docstring）；公开 API、普通函数与模块仍应符合上表。若报告中出现「（本次扫描无缺失项。）」表示按脚本规则当前无缺口。

**模块首行约定**：模块 docstring 须为文件**首条**语句（须写在 ``from __future__ import annotations`` 之前），否则 CPython 不将其视为 ``__doc__``，脚本也会判为「模块 docstring 缺失」。

报告文件按需生成：``docs/docstring_inventory.md``（不提交到仓库；查看运行 ``python scripts/docstring_inventory.py`` 即可）。完整 scripts 索引见 [scripts/README.md](../scripts/README.md)。

### 注释与文档（分层约定）

| 层级 | 要求 |
|------|------|
| 模块 | 文件顶部 docstring：职责、主要依赖、async/线程假设；复杂包注明与 ARCHITECTURE 的对应章节 |
| 公开 API | 类与公开函数：中文 docstring；参数、副作用、可能异常按需说明 |
| 复杂分支 | 如 CLI 主循环、命令调度、ReAct、多实例：关键分支旁简短说明「为何如此」 |
| 避免 | 逐行复述代码；大段迁移史放在 CHANGELOG / ARCHITECTURE；显而易见的代码不要写长篇 docstring |

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
feat: 添加 /status 命令支持飞书和 CLI
fix: 修复 session_manager 磁盘会话解析
docs: 补全三层记忆系统文档
refactor: 拆分 unified.py 为 engine/ 包
```

## 发布流程

1. 更新 `CHANGELOG.md`
2. 更新 `miniagent/__init__.py` 中的 `__version__`
3. 运行全量测试（见 [INDEX.md §测试与质量](INDEX.md#测试与质量)）
4. 提交并打 tag

## 软件工程实践（仓库卫生）

质量门禁、单一事实来源、状态目录与 Git 忽略规则等完整约定见 **[ENGINEERING.md](ENGINEERING.md)**。

### 文档与版本对齐清单（发版或大范围文档改动时）

1. ``miniagent/__init__.py`` 中 ``__version__`` 已与 ``CHANGELOG``、主要 ``docs/*.md`` 顶部版本标语一致。
2. ``README.md`` 中项目结构与实际仓库一致（已移除的文件勿再列出）。
3. 涉及行为变更时同步 ``ARCHITECTURE.md`` / 专题文档（如多实例注册表见 [ENGINEERING.md](ENGINEERING.md) §3.3）。
4. ``README.md`` 中的命令、测试数量与 CI 保持可验证（测试数以 ``pytest --collect-only`` 为准）。

### 文档写作约定

- **页眉**：``> Mini Agent Python | 版本: x.y.z | 最后更新: YYYY-MM-DD | 与 miniagent.__version__ 对齐``；**仅** [INDEX.md](INDEX.md) 与 [USER_GUIDE.md](USER_GUIDE.md) 追加「未发版行为见 CHANGELOG `[Unreleased]`」注记；[README.md](../README.md) §配置 的升级迁移提示除外。
- **SSOT**：同一主题只在一处写全；卫星文档用 1–3 句摘要 + 链接。对照表见 [ENGINEERING.md §1](ENGINEERING.md#1-单一事实来源single-source-of-truth) 与 [INDEX.md §SSOT 速查](INDEX.md#ssot-速查单一事实来源)。
- **交叉引用**：优先 ``[文档名](路径) §节号`` 或 markdown 锚点（如 ``[FEISHU.md §通道绑定](FEISHU.md#通道绑定)``）；深度专题用 Part/§，用户指南用章号。
- **路径术语**：首次出现 ``{paths.state_dir}/sessions/`` 等简写时，脚注「canonical 路径见 [ENGINEERING.md §3](ENGINEERING.md#3-状态目录与测试隔离)」。
- **代码示例**：与用户配置相关时以 ``config.user.json`` 为准；勿文档化代码库中不存在的错误码或 env 变量。
- **风格例外**：[PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) 使用「一、二、三」中文序号；[TROUBLESHOOTING.md](TROUBLESHOOTING.md) 小节标题可用 ❌/⚠️/🔧 前缀便于扫描。
- **pytest 命令**：完整收集/覆盖率命令以 [INDEX.md](INDEX.md) §测试与质量 为 SSOT；其它文档一句 + 链接即可。

---

## Part 2 — 扩展开发

### 工具扩展

#### ToolBuilder 使用方法

Mini Agent Python 使用 ToolBuilder 设计模式，提供链式调用 API，减少约 67% 代码量。

```python
from miniagent.tools.base import ToolBuilder

def register_my_toolbox(registry):
    toolbox = ToolBuilder("my_toolbox")

    toolbox.add_tool(
        name="read_config",
        description="读取配置文件",
        handler=read_config_handler,
    ).param(
        "path", "string", "配置文件路径",
        required=True,
    ).help("读取 JSON/YAML 配置文件并返回解析结果")

    toolbox.add_tool(
        name="write_config",
        description="写入配置文件",
        handler=write_config_handler,
    ).param("path", "string", "配置文件路径", required=True).param(
        "data", "object", "配置数据", required=True,
    )

    registry.register(toolbox.build())
```

#### 工具注册流程

**步骤 1：定义工具处理器**：

```python
async def read_config_handler(args: dict, ctx: ToolContext) -> ToolResult:
    path = args["path"]
    if not ctx.is_path_allowed(path):
        return ToolResult(status="error", content=f"路径 {path} 不在允许列表", error="Permission denied")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ToolResult(status="success", content=json.dumps(data, indent=2))
    except Exception as e:
        return ToolResult(status="error", content=f"读取失败: {e}", error=str(e))
```

**步骤 2–3：构建并注册**：内置工具箱由 `build_skill_snapshots` 自动合并；自定义工具应通过**技能包**或在 `register_builtin_tools` 之后向 `registry` 注册：

```python
from miniagent.tools.my_tools import MY_CUSTOM_TOOL

def register_my_tool(registry):
    registry.register("my_tool", MY_CUSTOM_TOOL)
```

#### 工具 Schema 定义

**基础参数类型**：`string`、`number`、`integer`、`boolean`、`object`、`array`

```python
toolbox.add_tool(...).enum_param("format", "输出格式", ["json", "yaml"], default="json")
toolbox.add_tool(...).param("encoding", "string", "编码格式", required=False, default="utf-8")
```

#### 低层 Tool 对象注册（API 场景）

若只需快速注册单个工具而不使用 ToolBuilder，可直接构造 `Tool` 对象：

```python
from miniagent.types.tool import Tool

tool = Tool(
    name="my_custom_tool",
    description="自定义文本处理工具",
    function=my_custom_tool,
    parameters={
        "type": "object",
        "properties": {"input": {"type": "string", "description": "要处理的文本"}},
        "required": ["input"],
    },
)
registry.register(tool)
```

生产扩展优先使用 ToolBuilder + 技能包。

### 技能扩展

#### 技能目录结构（推荐：`SKILL.md`）

```
workspaces/skills/my_skill/
├── SKILL.md            # 技能元数据 + 指令（YAML frontmatter + Markdown body）
├── skills/             # 子技能目录（可选）
│   └── my_tool/
│       ├── SKILL.md
│       └── tools.py    # 子技能工具实现（可选）
└── README.md           # 技能说明文档（可选）
```

加载器优先读取 `SKILL.md`（见 `miniagent/skills/loader.py`）。**备选格式**（遗留）：`skill.yaml` + `instructions.md`，新项目请勿使用。

#### SKILL.md 示例

```markdown
---
name: my_skill
description: 我的自定义技能
version: 1.0.0
---

# 我的技能

在此编写 stable system augment 指令正文……
```

#### 指令注入

`SKILL.md` body（或备选格式的 `instructions.md`）作为 stable system augment 放入 Agent 的第一条 `system` 消息，格式：`[SKILL: my_skill]\n{content}`。不要把本轮用户任务、记忆检索、知识库结果、当前时间或文件根目录写入 skill prompt。

### 通道扩展

```python
class ChannelProtocol(Protocol):
    async def send_message(self, content: str, *, session_key: str, chat_id: str | None = None) -> None: ...
    async def receive_message(self, *, session_key: str) -> dict | None: ...
```

实现消息处理回调时：解析消息 → 映射 `session_key` → 调用 `run_agent_with_thinking` → 发送回复。详见 [FEISHU.md §通道绑定](FEISHU.md#通道绑定)、[ARCHITECTURE.md](ARCHITECTURE.md)。

### 扩展单元测试

```python
@pytest.mark.asyncio
async def test_read_config_success(tool_context):
    args = {"path": "/tmp/test_workspace/config.json"}
    result = await read_config_handler(args, tool_context)
    assert result.status == "success"
```

### 添加新 CLI 命令

1. 在 `miniagent/engine/cli_commands.py` 添加 `cmd_<name>()` 函数
2. 在 `miniagent/engine/command_dispatch.py` 注册路由
3. 在 `cmd_help()` 添加帮助文本
4. CLI 和飞书自动共享新命令

---

## Part 3 — API 编程示例

进程级依赖集中在 [`RuntimeContext`](../miniagent/runtime/context.py)（组合根）；`registry` 经容器注入后传给 `run_agent()`。自我优化 API 见 [SELF_OPT.md](SELF_OPT.md)。

### 基础用法

#### 简单命令执行（内置工具）

```python
from miniagent.core.agent import run_agent
from miniagent.infrastructure.container import bootstrap_default_factories, get_tool_registry
from miniagent.engine.builtin_tools import register_builtin_tools

bootstrap_default_factories()
registry = get_tool_registry()
register_builtin_tools(registry)

result = await run_agent(user_input="读取README.md文件", registry=registry)
print(result.reply)
```

#### 最小测试注册表

单元测试或无需完整子系统时，可直接构造空注册表（与 `run_agent` docstring 示例一致）：

```python
from miniagent.core.agent import run_agent
from miniagent.infrastructure.registry import DefaultToolRegistry

registry = DefaultToolRegistry()
result = await run_agent("帮我分析当前目录", registry=registry, session_key="test-001")
```

#### 会话管理

```python
result1 = await run_agent(
    user_input="当前目录有什么文件？",
    registry=registry,
    session_key="my-session-123",
)
result2 = await run_agent(
    user_input="读取README.md文件",
    registry=registry,
    session_key="my-session-123",
)
```

#### 流式输出回调

```python
async def my_thinking_callback(text: str, streaming: bool, header: str, **kwargs) -> None:
    if streaming:
        print(f"[{header}] {text}")

result = await run_agent(
    user_input="分析代码性能",
    registry=registry,
    on_thinking=my_thinking_callback,
)
```

### 配置管理

#### 自定义 Agent 配置

```python
custom_config = {
    "max_turns": 200,
    "tool_timeout": 30,
    "allow_parallel_tools": False,
    "debug": True,
}
result = await run_agent(user_input="执行任务", registry=registry, agent_config=custom_config)
```

#### 自定义系统提示词

```python
result = await run_agent(
    user_input="审查src/main.py代码",
    registry=registry,
    system_prompt="你是一个专业的代码审查助手...",
)
```

`system_prompt` 作为 stable system augment；动态资料（检索结果、当前时间等）由执行器放入 current turn user context。

#### 配置文件管理

```python
from miniagent.infrastructure.json_config import get_config

max_turns = get_config("agent.max_turns", 400)
```

### 高级用法

#### 从 RuntimeContext 注入

与 `python -m miniagent` 入口一致：先经 `unified_entry` 构造并 `set_runtime_context(ctx)`，或在测试中手动组装 `RuntimeContext`：

```python
from miniagent.runtime.context import get_runtime_context
from miniagent.core.agent import run_agent

ctx = get_runtime_context()  # 须已初始化
result = await run_agent(
    user_input="分析代码性能",
    registry=ctx.registry,
    session_key="perf-analysis-session",
    agent_config={"max_turns": 50},
    client=ctx.openai_client,
    memory_store=ctx.memory_store,
    activity_log=ctx.activity_log,
)
```

#### 工具执行回调

```python
async def my_tool_finish_callback(tool_name, args_json, result, success, thinking_header):
    print(f"[{thinking_header}] 工具 {tool_name} {'成功' if success else '失败'}")

result = await run_agent(
    user_input="读取README.md",
    registry=registry,
    on_tool_finish=my_tool_finish_callback,
)
```

#### 跳过规划阶段

```python
result = await run_agent(user_input="读取README.md", registry=registry, skip_planning=True)
```

### API 测试场景

#### Mock 工具注册表

```python
from unittest.mock import Mock
from miniagent.infrastructure.container import set_tool_registry

mock_registry = Mock()
set_tool_registry(mock_registry)
result = await run_agent(user_input="测试命令", registry=mock_registry)
```

#### Mock LLM 客户端

```python
mock_client = AsyncMock(spec=AsyncOpenAI)
mock_client.chat.completions.create.return_value = MockLLMResponse(content="Mock response")
result = await run_agent(user_input="测试输入", registry=registry, client=mock_client)
```

#### 测试工具执行器

```python
from miniagent.core.executor_tools import execute_tools_concurrent

results = await execute_tools_concurrent(
    pending_calls=pending_calls,
    agent_config=mock_config,
    context=mock_context,
    session_key="test-session",
    thinking_header="[测试]",
    monitor=mock_monitor,
    activity_log=mock_activity_log,
)
```

### 错误处理

```python
try:
    result = await run_agent(user_input="执行任务", registry=registry)
except ContextBudgetExceeded as e:
    print(f"上下文超限: {e}")
except LLMError as e:
    print(f"LLM错误: {e}")
except ToolExecutionError as e:
    print(f"工具错误: {e}")
```

循环检测拦截时，结果以 `WARNING_PREFIX` 开头。

---

## 最佳实践与开发清单

### 设计原则

- **工具**：单一职责、异步处理、沙箱检查、清晰错误信息
- **技能**：指令清晰、README 完整、通过 `/reload-skills` 热加载验证
- **通道**：Protocol 解耦、ChannelRouter 统一管理会话映射
- **API**：通过 `run_agent()` 关键字参数注入依赖；`registry` 来自 `get_tool_registry()` 或 `RuntimeContext`；模块化引用 `executor_*` 子模块

### 开发清单

| 类型 | 步骤 |
|------|------|
| 工具 | 定义 handler → ToolBuilder 构建 Schema → 沙箱检查 → 单元测试 → 更新文档 |
| 技能 | 创建 SKILL.md（frontmatter + 指令）→ 实现 tools.py（可选）→ `/reload-skills` 验证 → 集成测试 |
| 通道 | 定义 ChannelProtocol → 实现回调 → 配置会话同步 → 更新 ARCHITECTURE |
| CLI 命令 | `cli_commands.py` → `command_dispatch.py` → `cmd_help()` → 测试 |
| API 集成 | `bootstrap_default_factories` + `run_agent` → 配置 `session_key` → 添加回调 → Mock 测试 |

---

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构设计
- [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) — 记忆系统
- [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) — 知识库与 RAG
- [ENGINEERING.md](ENGINEERING.md) — 质量门禁与仓库卫生
- [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) — 提示词编写规范
