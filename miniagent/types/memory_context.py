"""Mini Agent Python — 记忆上下文抽象接口

本模块定义记忆注入和管理的抽象接口，用于解除核心层（core）对记忆层（memory）的直接依赖，
遵循依赖倒置原则（DIP）。

核心层通过这些 Protocol 接口与记忆系统交互，具体实现由 RuntimeContext 注入，
避免核心层反向依赖记忆层的具体实现。

**设计原则**：
- 所有 Protocol 仅定义接口，不包含实现
- 实现类分布在 miniagent/memory/ 模块中
- 核心层通过依赖注入接收实现实例

**使用示例**：
```python
# executor.py - 核心层
from miniagent.types.memory_context import MemoryContextProtocol

async def execute_plan(
    ...,
    memory_context: MemoryContextProtocol | None = None,
):
    if memory_context:
        messages, mem_meta = await memory_context.inject_memory_to_messages(...)
```

```python
# runtime/context.py - 组合根
from miniagent.memory import context as memory_context_impl

class RuntimeContext:
    def __init__(self, ...):
        self.memory_context = memory_context_impl  # 注入实现
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from miniagent.types.config import AgentConfig
    from miniagent.types.tool import ToolRegistryProtocol


class MemoryInjectionResult(dict):
    """记忆注入结果（继承 dict 以兼容现有代码）"""

    def __init__(
        self,
        messages: list[dict],
        memory_metadata: dict[str, Any],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.messages = messages
        self.memory_metadata = memory_metadata


class MemoryContextProtocol(Protocol):
    """记忆注入接口抽象

    定义核心层与记忆系统的交互接口，用于：
    - 向 LLM 消息序列注入三层记忆
    - 在执行后保存新的记忆

    实现类：miniagent.memory.context.DefaultContextManager
    """

    async def inject_memory_to_messages(
        self,
        messages: list[dict],
        session_key: str,
        agent_config: "AgentConfig",
        *,
        tool_registry: "ToolRegistryProtocol | None" = None,
        user_input: str | None = None,
        activity_log: Any | None = None,
        keyword_index: Any | None = None,
    ) -> tuple[list[dict], dict[str, Any]]:
        """注入三层记忆到消息序列

        Args:
            messages: 原始消息序列
            session_key: 会话标识
            agent_config: Agent 配置
            tool_registry: 工具注册表（可选）
            user_input: 用户输入（可选）
            activity_log: 活动日志实例（可选）
            keyword_index: 关键词索引实例（可选）

        Returns:
            tuple[list[dict], dict[str, Any]]:
                - messages: 注入记忆后的消息序列
                - memory_metadata: 记忆元数据（用于后续处理）
        """
        ...

    async def save_memory_after_turn(
        self,
        session_key: str,
        user_input: str,
        reply: str,
        memory_store: Any,
        *,
        tool_calls: list[dict] | None = None,
        token_usage: dict | None = None,
    ) -> None:
        """保存执行后的记忆

        Args:
            session_key: 会话标识
            user_input: 用户输入
            reply: Agent 回复
            memory_store: 记忆存储实例
            tool_calls: 工具调用记录（可选）
            token_usage: Token 使用统计（可选）
        """
        ...


class MemorySearchProtocol(Protocol):
    """记忆搜索接口抽象

    定义记忆检索的接口，用于：
    - 关键词索引搜索
    - 嵌入向量搜索

    实现类：miniagent.memory.keyword_index.KeywordIndex
    """

    async def search_relevant_memory(
        self,
        query: str,
        session_key: str,
        *,
        top_k: int = 5,
    ) -> list[dict]:
        """搜索相关记忆

        Args:
            query: 搜索查询
            session_key: 会话标识
            top_k: 返回结果数量

        Returns:
            list[dict]: 搜索结果列表
        """
        ...

    def format_search_results(
        self,
        results: list[dict],
        *,
        max_length: int | None = None,
    ) -> str:
        """格式化搜索结果

        Args:
            results: 搜索结果列表
            max_length: 最大长度（可选）

        Returns:
            str: 格式化后的结果文本
        """
        ...


class MemoryHistoryProtocol(Protocol):
    """历史处理接口抽象

    定义会话历史的处理接口，用于：
    - 加载历史消息
    - 格式化历史为 LLM 输入

    实现类：miniagent.memory.history_bridge
    """

    async def load_history(
        self,
        session_key: str,
        *,
        max_messages: int | None = None,
    ) -> list[dict]:
        """加载会话历史

        Args:
            session_key: 会话标识
            max_messages: 最大消息数（可选）

        Returns:
            list[dict]: 历史消息列表
        """
        ...

    def format_history_for_llm(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
    ) -> list[dict]:
        """格式化历史为 LLM 输入

        Args:
            messages: 历史消息列表
            max_tokens: 最大 Token 数（可选）

        Returns:
            list[dict]: 格式化后的消息列表
        """
        ...


__all__ = [
    "MemoryInjectionResult",
    "MemoryContextProtocol",
    "MemorySearchProtocol",
    "MemoryHistoryProtocol",
]