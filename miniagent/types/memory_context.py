"""Mini Agent Python — 记忆上下文抽象接口

本模块定义记忆上下文处理和管理的抽象接口，用于解除核心层（core）对记忆层（memory）的直接依赖，
遵循依赖倒置原则（DIP）。

核心层通过这些 Protocol 接口与记忆系统交互，具体实现由 ``ApplicationContainer`` 注入
（``miniagent.memory.memory_context_service.DefaultMemoryContext``），
避免核心层反向依赖记忆层的具体实现。

**设计原则**：
- 所有 Protocol 仅定义接口，不包含实现
- 实现类位于 ``miniagent/memory/memory_context_service.py``
- 核心层通过依赖注入接收实现实例

**主路径行为**（``executor.execute_plan``）：
- 动态记忆不写入 system prompt，而由 ``inject_memory_to_messages`` 返回的
  ``memory_metadata["turn_keyword_context"]`` 合并进 current turn user context
- 消息顺序保持 ``stable system → history → current user context``

**使用示例**：
```python
# executor.py - 核心层
from miniagent.types.memory_context import MemoryContextProtocol

async def execute_plan(..., memory: MemoryRuntimeProtocol):
    _, mem_meta = await memory.context.inject_memory_to_messages(
        [], session_key, agent_config, user_input=user_input
    )
    turn_ctx = mem_meta.get("turn_keyword_context")
```

```python
# bootstrap.entrypoint - composition root
from miniagent.memory.memory_context_service import create_default_memory_context

memory = create_memory_runtime()
container = ApplicationContainer(..., memory=memory)
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from miniagent.types.config import AgentConfig
    from miniagent.types.tool import ToolRegistryProtocol


class MemoryInjectionResult(dict):
    """记忆上下文处理结果（继承 dict 以兼容现有代码）。

    Attributes:
        messages: 注入后的消息序列（主路径通常与输入相同）
        memory_metadata: 记忆元数据，含 ``turn_keyword_context``、``relevant`` 等键
    """

    def __init__(
        self,
        messages: list[dict],
        memory_metadata: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.messages = messages
        self.memory_metadata = memory_metadata

    @classmethod
    def from_tuple(
        cls,
        result: tuple[list[dict], dict[str, Any]],
    ) -> MemoryInjectionResult:
        messages, metadata = result
        return cls(messages=messages, memory_metadata=metadata)


@runtime_checkable
class MemoryContextProtocol(Protocol):
    """记忆上下文接口抽象

    定义核心层与记忆系统的交互接口，用于：
    - 生成本轮记忆/检索上下文字符串（``memory_metadata``）
    - 在执行后保存新的记忆

    当前执行主路径由 executor 直接构建 ``stable system -> history ->
    current turn user context``；本 Protocol 封装检索与持久化逻辑，
    供 ``ApplicationContainer`` 注入及测试替身使用。
    """

    async def inject_memory_to_messages(
        self,
        messages: list[dict],
        session_key: str,
        agent_config: AgentConfig,
        *,
        tool_registry: ToolRegistryProtocol | None = None,
        user_input: str | None = None,
        activity_log: Any | None = None,
        keyword_index: Any | None = None,
    ) -> tuple[list[dict], dict[str, Any]]:
        """构建本轮记忆上下文元数据。

        主路径不修改 ``messages``，而是在 ``memory_metadata`` 中返回：

        - ``memory_context``: Layer 2 结构化会话记忆文本
        - ``keyword_context``: Layer 3 检索结果格式化文本
        - ``turn_keyword_context``: 上述两者合并，供 current turn user context
        - ``relevant`` / ``relevant_count``: 原始检索条目与数量

        Args:
            messages: 原始消息序列（通常为空或占位，主路径透传）
            session_key: 会话标识
            agent_config: Agent 配置
            tool_registry: 工具注册表（可选，供扩展实现）
            user_input: 用户输入，用于语义检索
            activity_log: 活动日志实例（可选）
            keyword_index: 覆盖默认关键词索引（可选）

        Returns:
            tuple[list[dict], dict[str, Any]]: 消息序列与记忆元数据

        Note:
            后台子 session（``__bg__*``）跳过 Layer 2/3 注入。
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
            token_usage: Token 使用统计（可选，预留扩展）

        Note:
            后台子 session（``__bg__*``）不落盘。
        """
        ...


@runtime_checkable
class MemorySearchProtocol(Protocol):
    """记忆搜索接口抽象

    定义记忆检索的接口，用于：
    - 关键词索引搜索（``KeywordIndex`` / ``search_relevant_with_index``）
    - 嵌入向量搜索（``EmbeddingSearchProvider``，若已启用）

    实现类：``miniagent.memory.memory_context_service.DefaultMemorySearch``
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
            session_key: 会话标识（保留供扩展实现按会话过滤；默认跨会话检索）
            top_k: 返回结果数量

        Returns:
            list[dict]: 搜索结果列表，含 ``session_id``、``summary``、``score`` 等
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
            max_length: 输出文本最大字符数（可选）

        Returns:
            str: 格式化后的结果文本
        """
        ...


@runtime_checkable
class MemoryHistoryProtocol(Protocol):
    """历史处理接口抽象

    定义会话历史的处理接口，用于：
    - 加载历史消息
    - 格式化历史为 LLM 输入

    实现类：``miniagent.memory.memory_context_service.DefaultMemoryHistory``
    底层格式化：``miniagent.memory.history_bridge.format_history_for_llm``
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
            max_messages: 最大消息数（从末尾截取，可选）

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
            max_tokens: 最大 Token 预算（可选，超出时从头部丢弃）

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
