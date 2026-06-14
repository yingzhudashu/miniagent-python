# Mini Agent Python 扩展开发指南

> 版本: 2.1.0 | 最后更新: 2026-06-09

本指南提供扩展开发的方法和规范，帮助开发者创建自定义工具、技能和通道。

---

## 目录

1. [工具扩展](#工具扩展)
2. [技能扩展](#技能扩展)
3. [通道扩展](#通道扩展)
4. [测试规范](#测试规范)
5. [最佳实践](#最佳实践)

---

## 工具扩展

### ToolBuilder 使用方法

Mini Agent Python 使用 ToolBuilder 设计模式，提供链式调用 API，减少约 67% 代码量。

**基础示例**：
```python
from miniagent.tools.base import ToolBuilder

def register_my_toolbox(registry):
    toolbox = ToolBuilder("my_toolbox")

    # 添加工具1
    toolbox.add_tool(
        name="read_config",
        description="读取配置文件",
        handler=read_config_handler,
    ).param(
        "path", "string", "配置文件路径",
        required=True,
    ).help("读取 JSON/YAML 配置文件并返回解析结果")

    # 添加工具2
    toolbox.add_tool(
        name="write_config",
        description="写入配置文件",
        handler=write_config_handler,
    ).param(
        "path", "string", "配置文件路径",
        required=True,
    ).param(
        "data", "object", "配置数据",
        required=True,
    )

    # 注册到工具箱
    registry.register(toolbox.build())
```

---

### 工具注册流程

**步骤 1：定义工具处理器**：
```python
async def read_config_handler(args: dict, ctx: ToolContext) -> ToolResult:
    """工具处理器实现

    Args:
        args: 工具参数（已验证）
        ctx: 工具上下文（包含 cwd、allowed_paths等）

    Returns:
        ToolResult: 工具执行结果
    """
    path = args["path"]

    # 1. 检查路径安全（沙箱限制）
    if not ctx.is_path_allowed(path):
        return ToolResult(
            status="error",
            content=f"路径 {path} 不在允许列表",
            error="Permission denied",
        )

    # 2. 执行工具逻辑
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return ToolResult(
            status="success",
            content=json.dumps(data, indent=2),
            metadata={"format": "json", "size": len(data)},
        )
    except Exception as e:
        return ToolResult(
            status="error",
            content=f"读取失败: {e}",
            error=str(e),
        )
```

---

**步骤 2：构建工具箱**：
```python
# 在 miniagent/tools/my_tools.py 中
from miniagent.infrastructure.registry import DefaultToolRegistry
from miniagent.tools.base import ToolBuilder

def register_my_toolbox(registry: DefaultToolRegistry):
    toolbox = ToolBuilder("my_toolbox")

    # ... 添加工具（见上文）

    registry.register(toolbox.build())
```

---

**步骤 3：注册工具箱**：

内置工具箱由 ``build_skill_snapshots`` 自动合并；自定义工具应通过 **技能包** 或
在 ``register_builtin_tools`` 之后向 ``registry`` 注册。勿直接修改 ``init_subsystems``，
除非你在维护引擎本身：

```python
# 扩展方式示例：在技能包 tools.py 中注册，或由 /reload-skills 热加载
from miniagent.tools.my_tools import MY_CUSTOM_TOOL

def register_my_tool(registry):
    registry.register("my_tool", MY_CUSTOM_TOOL)
```

---

### 工具 Schema 定义

**基础参数类型**：
- `"string"` - 字符串
- `"number"` - 数字（整数或浮点）
- `"integer"` - 整数
- `"boolean"` - 布尔值
- `"object"` - JSON 对象
- `"array"` - JSON 数组

**高级参数类型**：
```python
# 枚举参数（限制可选值）
toolbox.add_tool(...).enum_param("format", "输出格式", ["json", "yaml"], default="json")

# 可选参数（非必填）
toolbox.add_tool(...).param("encoding", "string", "编码格式", required=False, default="utf-8")

# 数组参数
toolbox.add_tool(...).param("keys", "array", "要读取的键列表", required=True)

# 对象参数
toolbox.add_tool(...).param("options", "object", "配置选项", required=False)
```

---

## 技能扩展

### 技能目录结构

```
workspaces/skills/my_skill/
├── skill.yaml          # 技能元数据
├── instructions.md     # 技能指令（作为 stable system augment）
├── tools.py            # 技能工具实现（可选）
└── README.md           # 技能说明文档
```

---

### YAML Manifest 编写

**skill.yaml 示例**：
```yaml
name: my_skill
version: 1.0.0
description: 我的自定义技能
author: your_name

# 工具箱定义（可选）
toolboxes:
  - name: my_toolbox
    description: 我的工具箱
    tools:
      - name: my_tool_1
        description: 工具1说明
      - name: my_tool_2
        description: 工具2说明

# 指令文件（必需）
instructions: instructions.md

# 依赖（可选）
dependencies:
  - python_package: requests
    version: ">=2.28.0"
```

---

### 指令注入方法

**instructions.md 示例**：
```markdown
# My Skill Instructions

This skill provides custom tools for [describe purpose].

## Available Tools

### my_tool_1
Purpose: [describe tool purpose]
Usage: [provide usage examples]

### my_tool_2
Purpose: [describe tool purpose]
Usage: [provide usage examples]

## Best Practices

1. [provide best practices for using this skill]
2. [add usage guidelines]
```

**注入位置**：
- 指令内容会作为 stable system augment 放入 Agent 的第一条 `system` 消息
- 格式：`[SKILL: my_skill]\n{instructions.md content}`
- skill prompt 属于低频动态前缀：安装、刷新、切换 session、gating/config/env 变化后会形成新的 stable system 版本
- 不要把本轮用户任务、记忆检索结果、知识库结果、当前时间或文件根目录写入 skill prompt；这些动态资料由执行器放入 current turn user context

---

## 通道扩展

### 通道接口设计

**定义通道接口**：
```python
# miniagent/types/channel.py
from typing import Protocol

class ChannelProtocol(Protocol):
    """通道接口协议"""

    async def send_message(
        self,
        content: str,
        *,
        session_key: str,
        chat_id: str | None = None,
    ) -> None:
        """发送消息到通道"""
        ...

    async def receive_message(
        self,
        *,
        session_key: str,
    ) -> dict | None:
        """从通道接收消息"""
        ...
```

---

### 回调实现方法

**实现消息处理回调**：
```python
# miniagent/engine/telegram_handler.py
class TelegramHandler:
    """Telegram 通道处理器"""

    def __init__(self, ctx: RuntimeContext):
        self.ctx = ctx
        self.channel_type = "telegram"

    async def on_message_receive(self, message: dict):
        """消息接收回调"""
        # 1. 解析消息
        user_input = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        # 2. 映射到会话
        session_key = f"telegram:{chat_id}"

        # 3. 调用 Agent
        reply = await run_agent_with_thinking(
            user_input=user_input,
            session_key=session_key,
            ctx=self.ctx,
        )

        # 4. 发送回复
        await self.send_message(reply, chat_id=chat_id)
```

---

## 测试规范

### 单元测试示例

```python
# tests/test_my_tool.py
import pytest
from miniagent.tools.my_tools import read_config_handler
from miniagent.types.tool import ToolContext

@pytest.fixture
def tool_context():
    """提供测试上下文"""
    return ToolContext(
        cwd="/tmp/test_workspace",
        allowed_paths=["/tmp/test_workspace"],
    )

@pytest.mark.asyncio
async def test_read_config_success(tool_context):
    """测试成功读取配置"""
    # 准备测试数据
    test_path = "/tmp/test_workspace/config.json"

    # 执行工具
    args = {"path": test_path}
    result = await read_config_handler(args, tool_context)

    # 验证结果
    assert result.status == "success"
```

---

## 最佳实践

### 1. 工具设计原则

**单一职责**：
- 每个工具只做一件事
- 工具名称清晰描述功能
- 避免创建"超级工具"

**错误处理**：
- 明确区分用户误用和工具缺陷
- 提供清晰的错误信息
- 使用 `ToolResult.status` 标识状态

**性能优化**：
- 使用异步处理（`async def`）
- 添加超时保护
- 处理大数据时分批执行

---

### 2. 技能设计原则

**指令清晰**：
- 明确说明技能用途
- 提供使用示例
- 说明工具适用场景

**文档完整**：
- README.md 说明技能概述
- instructions.md 提供详细指令
- 更新 CHANGELOG 记录变更

---

### 3. 通道设计原则

**解耦设计**：
- 通道处理器仅接收必要接口
- 不依赖其他通道实现
- 使用 Protocol 定义接口

**状态隔离**：
- 每个通道独立管理状态
- 会话映射统一由 ChannelRouter 管理
- 避免跨通道状态泄漏

---

## 扩展开发清单

**工具开发**：
1. ✅ 定义工具处理器
2. ✅ 使用 ToolBuilder 构建 Schema
3. ✅ 添加沙箱检查和权限控制
4. ✅ 编写单元测试
5. ✅ 更新文档

**技能开发**：
1. ✅ 创建 skill.yaml 元数据
2. ✅ 编写 instructions.md 指令
3. ✅ 实现工具（可选）
4. ✅ 编写 README.md 说明
5. ✅ 测试完整集成

**通道开发**：
1. ✅ 定义 ChannelProtocol 接口
2. ✅ 实现消息处理回调
3. ✅ 配置会话状态同步
4. ✅ 测试通道集成
5. ✅ 更新架构文档

---

**持续改进**：扩展开发是一个迭代过程，建议定期审查和优化扩展实现。
