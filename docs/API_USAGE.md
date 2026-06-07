# MiniAgent API使用示例

本文档提供MiniAgent核心API的使用示例，涵盖基础用法、高级用法、配置管理、测试场景等。

## 目录

1. [基础用法](#基础用法)
2. [自定义工具](#自定义工具)
3. [配置管理](#配置管理)
4. [高级用法](#高级用法)
5. [测试场景](#测试场景)
6. [错误处理](#错误处理)

---

## 基础用法

### 1. 简单命令执行

最基础的用法：用户输入 → Agent执行 → 返回结果。

```python
from miniagent.core.agent import run_agent
from miniagent import get_global_tool_registry

# 获取工具注册表
registry = get_global_tool_registry()

# 执行简单命令
result = await run_agent(
    user_input="读取README.md文件",
    registry=registry,
)

print(result)
# 输出：README.md文件的内容摘要
```

### 2. 会话管理

使用session_key管理多轮对话：

```python
# 第一轮：用户提问
result1 = await run_agent(
    user_input="当前目录有什么文件？",
    registry=registry,
    session_key="my-session-123",
)

# 第二轮：继续对话（Agent记住之前的上下文）
result2 = await run_agent(
    user_input="读取README.md文件",
    registry=registry,
    session_key="my-session-123",  # 同一个session_key
)

print(result2)
# 输出：Agent会记得"当前目录有什么文件"的上下文
```

### 3. 流式输出回调

使用on_thinking回调实时显示Agent思考过程：

```python
async def my_thinking_callback(
    text: str,
    is_streaming: bool,
    phase_label: str,
    **kwargs,
) -> None:
    """实时显示Agent思考过程。"""
    if is_streaming:
        print(f"[{phase_label}] {text}")
    else:
        print(f"[{phase_label}] 开始...")

result = await run_agent(
    user_input="分析代码性能",
    registry=registry,
    on_thinking=my_thinking_callback,
)

# 输出示例：
# [评估与计划] 开始...
# [评估与计划] 任务难度：中等
# [执行] 正在读取代码文件...
# [执行] 正在分析性能瓶颈...
```

---

## 自定义工具

### 4. 注册自定义工具

创建并注册自定义工具到全局注册表：

```python
from miniagent.types.tool import Tool
from miniagent import get_global_tool_registry, set_global_tool_registry

# 定义自定义工具函数
def my_custom_tool(args: dict) -> str:
    """自定义工具实现。

    Args:
        args: 工具参数字典

    Returns:
        工具执行结果
    """
    input_text = args.get("input", "")
    return f"处理结果: {input_text}"

# 创建Tool对象
tool = Tool(
    name="my_custom_tool",
    description="自定义文本处理工具",
    function=my_custom_tool,
    parameters={
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "要处理的文本",
            },
        },
        "required": ["input"],
    },
)

# 注册到全局注册表
registry = get_global_tool_registry()
registry.register(tool)

# 使用自定义工具
result = await run_agent(
    user_input="使用my_custom_tool处理文本：Hello World",
    registry=registry,
)

print(result)
# 输出：处理结果: Hello World
```

### 5. 工具箱管理

创建工具箱（Toolbox）组织相关工具：

```python
from miniagent.types.toolbox import Toolbox

# 创建文件操作工具箱
file_toolbox = Toolbox(
    name="file_operations",
    description="文件读写操作工具箱",
    tools=[
        Tool(name="read_file", ...),
        Tool(name="write_file", ...),
        Tool(name="list_dir", ...),
    ],
)

# 创建数据分析工具箱
analysis_toolbox = Toolbox(
    name="data_analysis",
    description="数据分析工具箱",
    tools=[
        Tool(name="analyze_csv", ...),
        Tool(name="visualize_data", ...),
    ],
)

# 使用工具箱
result = await run_agent(
    user_input="读取README.md并分析内容",
    registry=registry,
    toolboxes=[file_toolbox, analysis_toolbox],
)
```

---

## 配置管理

### 6. 自定义Agent配置

覆盖默认Agent配置：

```python
from miniagent.types.agent_config import AgentConfig

# 创建自定义配置
custom_config = {
    "max_turns": 200,  # 最大执行轮数（默认400）
    "tool_timeout": 30,  # 工具超时时间（秒，默认60）
    "allow_parallel_tools": False,  # 禁止并发工具执行
    "debug": True,  # 启用调试日志
}

result = await run_agent(
    user_input="执行任务",
    registry=registry,
    agent_config=custom_config,
)
```

### 7. 自定义系统提示词

注入自定义系统提示词：

```python
custom_system_prompt = """
你是一个专业的代码审查助手。

职责：
- 分析代码质量
- 检查安全漏洞
- 提供改进建议

输出格式：
- 问题列表
- 修复建议
- 优先级排序
"""

result = await run_agent(
    user_input="审查src/main.py代码",
    registry=registry,
    system_prompt=custom_system_prompt,
)
```

### 8. 配置文件管理

使用配置文件管理全局配置：

```python
# config.user.json（用户自定义配置）
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4-turbo",
    "temperature": 0.7,
  },
  "agent": {
    "max_turns": 300,
    "tool_timeout": 45,
  },
  "memory": {
    "store_cache_max": 200,
  }
}

# 代码中自动加载配置
from miniagent.infrastructure.json_config import get_config

max_turns = get_config("agent.max_turns", 400)  # 优先读取用户配置
```

---

## 高级用法

### 9. 依赖注入（测试场景）

使用AgentContext dataclass简化参数传递（Phase 1重构新增）：

```python
from miniagent.types.agent_context import AgentContext

# 创建Agent上下文
context = AgentContext(
    user_input="分析代码性能",
    registry=registry,
    session_key="perf-analysis-session",
    agent_config={"max_turns": 50},
    on_thinking=my_thinking_callback,
)

# 执行（参数合并为单一对象）
result = await run_agent_from_context(context)

# 或者转换为kwargs向后兼容
kwargs = context.to_kwargs()
result = await run_agent(**kwargs)
```

### 10. 工具执行回调

监控工具执行过程：

```python
async def my_tool_finish_callback(
    tool_name: str,
    args_json: str,
    result: str,
    success: bool,
    thinking_header: str,
) -> None:
    """工具执行完成回调。"""
    status = "成功" if success else "失败"
    print(f"[{thinking_header}] 工具 {tool_name} 执行{status}")
    print(f"参数: {args_json}")
    print(f"结果: {result[:100]}...")

result = await run_agent(
    user_input="读取README.md",
    registry=registry,
    on_tool_finish=my_tool_finish_callback,
)

# 输出示例：
# [执行] 工具 read_file 执行成功
# 参数: {"path": "README.md"}
# 结果: # MiniAgent Python...
```

### 11. 跳过规划阶段

直接执行阶段（跳过Phase 1规划）：

```python
result = await run_agent(
    user_input="读取README.md",
    registry=registry,
    skip_planning=True,  # 跳过规划阶段
)

# 适用场景：
# - 任务明确无需规划
# - 简单工具调用
# - 性能优化（减少LLM调用）
```

---

## 测试场景

### 12. Mock工具注册表

测试时注入mock工具注册表：

```python
from unittest.mock import Mock
from miniagent import set_global_tool_registry

# 创建mock工具注册表
mock_registry = Mock()
mock_registry.get.return_value = MockTool()
mock_registry.list.return_value = ["mock_tool"]

# 注入mock（测试期间）
set_global_tool_registry(mock_registry)

# 现在get_global_tool_registry()返回mock实例
result = await run_agent(
    user_input="测试命令",
    registry=mock_registry,
)

# 恢复原始注册表（测试后）
original_registry = get_global_tool_registry()
```

### 13. Mock LLM客户端

测试时注入mock LLM客户端：

```python
from unittest.mock import AsyncMock
from openai import AsyncOpenAI

# 创建mock LLM客户端
mock_client = AsyncMock(spec=AsyncOpenAI)
mock_client.chat.completions.create.return_value = MockLLMResponse(
    content="Mock LLM response",
    tool_calls=None,
)

# 使用mock客户端
result = await run_agent(
    user_input="测试输入",
    registry=registry,
    client=mock_client,
)
```

### 14. 测试工具执行器

单独测试executor模块（Phase 1重构后）：

```python
from miniagent.core.executor_tools import execute_tools_concurrent
from miniagent.types.tool import ToolResult

# 创建测试工具调用
pending_calls = [
    (MockToolCall("read_file", {"path": "test.txt"}), {"path": "test.txt"}, mock_tool),
]

# 测试工具执行器
results = await execute_tools_concurrent(
    pending_calls=pending_calls,
    agent_config=mock_config,
    context=mock_context,
    session_key="test-session",
    thinking_header="[测试]",
    monitor=mock_monitor,
    activity_log=mock_activity_log,
)

# 验证结果
assert len(results) == 1
assert results[0][3].success  # ToolResult.success
```

---

## 错误处理

### 15. 异常捕获

捕获Agent执行异常：

```python
try:
    result = await run_agent(
        user_input="执行任务",
        registry=registry,
        agent_config={"max_turns": 10},
    )
except ContextBudgetExceeded as e:
    # 上下文token超预算
    print(f"上下文超限: {e}")
    # 处理：压缩历史或新开会话
except LLMError as e:
    # LLM调用失败
    print(f"LLM错误: {e}")
    # 处理：重试或降级
except ToolExecutionError as e:
    # 工具执行失败
    print(f"工具错误: {e}")
    # 处理：检查工具配置
except Exception as e:
    # 其他异常
    print(f"未知错误: {e}")
    # 处理：记录日志并通知用户
```

### 16. 循环检测处理

处理循环检测拦截：

```python
from miniagent.types.error_prefix import WARNING_PREFIX

result = await run_agent(
    user_input="重复调用同一个工具",
    registry=registry,
)

if result.startswith(WARNING_PREFIX):
    # Agent返回警告消息（循环检测拦截）
    print(f"警告: {result}")
    # 处理：简化任务或明确目标
```

---

## 最佳实践

### 参数数量控制

使用AgentContext dataclass控制参数数量（Clean Code原则）：

```python
# 不推荐：直接传递大量参数
result = await run_agent(
    user_input="...",
    registry=...,
    monitor=...,
    toolboxes=...,
    agent_config=...,
    system_prompt=...,
    skip_planning=...,
    on_tool_call=...,
    on_tool_finish=...,
    on_plan=...,
    on_thinking=...,
    clawhub=...,
    memory_store=...,
    activity_log=...,
    keyword_index=...,
    client=...,
    clarifier=...,
    session_key=...,
    confirmation_channel=...,
    engine=...,
)

# 推荐：使用AgentContext合并参数
context = AgentContext(
    user_input="...",
    registry=...,
    session_key=...,
    on_thinking=...,
)

result = await run_agent(**context.to_kwargs())  # 参数≤10
```

### 模块化设计

Phase 1重构后的模块化架构：

```python
# executor拆分模块（清晰职责）
from miniagent.core.executor_streaming import StreamHandler
from miniagent.core.executor_tools import execute_tools_concurrent
from miniagent.core.executor_memory import retrieve_memory_parallel
from miniagent.core.executor_react import ReActLoop

# 灵活组合使用
stream_handler = StreamHandler(context_manager, agent_config, session_key)
react_loop = ReActLoop(agent_config, context_manager, loop_detector)
memory_manager = MemoryManager(memory_store, activity_log)

# 独立测试各模块
await stream_handler.stream_exec_turn(...)
await execute_tools_concurrent(...)
await retrieve_memory_parallel(...)
```

### 性能优化

利用并行执行优化性能：

```python
# 并行工具执行（默认开启）
custom_config = {
    "allow_parallel_tools": True,  # 并发执行多个工具
    "tool_timeout": 60,  # 单工具超时（秒）
}

# 并行记忆检索（executor_memory.py自动并行）
result = await run_agent(
    user_input="...",
    registry=registry,
    agent_config=custom_config,
)

# 性能基准：
# - 单工具执行：~150ms
# - 并行3工具：~200ms（vs顺序450ms，节省55%）
# - 记忆并行检索：~70ms（vs顺序120ms，节省40%）
```

---

## 相关文档

- docs/ARCHITECTURE.md - 系统架构设计
- docs/MEMORY_SYSTEM.md - 记忆系统使用指南
- docs/KNOWLEDGE_BASE.md - 知识库与 RAG 集成（已全面集成）
- docs/INSTANCE_REGISTRY.md - 实例注册表文档

## 变更历史

- 2026-06-05: 创建文档（Phase 4任务）
- 2026-06-05: Phase 1重构完成（executor/main拆分、AgentContext）

---

**提示**: 本文档随代码重构持续更新。如遇API变更，请参考最新版本的docs/API_USAGE.md。