"""Mini Agent Python — 工具注册表

ToolRegistry 是 Mini Agent 的核心子系统之一，负责管理所有工具的生命周期。
实现 :class:`miniagent.agent.types.tool.ToolRegistryProtocol`。

架构设计：
    register()   → 添加工具
    unregister() → 移除工具
    get()        → 查询单个工具
    get_all()    → 获取全部工具
    get_schemas() → 提取 OpenAI schema 列表
    list()       → 获取工具名称列表

    get_schemas_by_toolboxes() → 按工具箱筛选
    get_by_toolboxes()         → 按工具箱筛选

使用字典存储的原因：
- 查找/插入/删除都是 O(1)
- 工具名称天然具有唯一性，适合作为 key
- 保持插入顺序（Python 3.7+ dict 有序）

工具箱筛选机制：
- 每个工具可选绑定一个 toolbox ID（如 "file_read"）
- 不绑定 toolbox 的工具始终可用（相当于 core 能力）
- get_schemas_by_toolboxes() 用于 Phase 2 执行阶段动态筛选工具
"""

from __future__ import annotations

import builtins
import collections
from collections.abc import Sequence
from typing import Any

ChatCompletionToolParam = dict[str, Any]

from miniagent.agent.types.tool import RegisteredTool, ToolDefinition, ToolRegistryProtocol

_TOOLBOX_SCHEMA_CACHE_MAX = 128


class DefaultToolRegistry(ToolRegistryProtocol):
    """默认工具注册表实现

    使用字典作为内部存储。

    Example:
        registry = DefaultToolRegistry()
        registry.register("read_file", read_file_tool)
        registry.register("write_file", write_file_tool)

        # 获取所有工具的 OpenAI schema
        schemas = registry.get_schemas()

        # 按工具箱筛选
        file_schemas = registry.get_schemas_by_toolboxes(["file_read", "file_write"])
    """

    def __init__(self) -> None:
        """初始化空注册表"""
        self._tools: dict[str, RegisteredTool] = {}
        self._schema_cache: list[ChatCompletionToolParam] | None = None
        self._toolbox_schema_cache: collections.OrderedDict[
            frozenset[str],
            list[ChatCompletionToolParam],
        ] = collections.OrderedDict()

    def register(self, name: str, tool: ToolDefinition) -> None:
        """注册一个工具

        将 ToolDefinition 包装为 RegisteredTool（增加 name 字段），
        存入内部字典。如果工具名称已存在，抛出异常防止重复注册。

        Args:
            name: 工具名称（如 "read_file"、"write_file"）
            tool: 工具定义

        Raises:
            ValueError: 如果工具名称已注册
        """
        if name in self._tools:
            raise ValueError(f'Tool "{name}" already registered')
        self._tools[name] = RegisteredTool(
            name=name,
            schema=tool.schema,
            handler=tool.handler,
            permission=tool.permission,
            help_text=tool.help_text,
            toolbox=tool.toolbox,
        )
        self._schema_cache = None
        self._toolbox_schema_cache.clear()

    def unregister(self, name: str) -> bool:
        """注销一个工具

        Args:
            name: 工具名称

        Returns:
            True 如果成功移除，False 如果工具不存在
        """
        if name in self._tools:
            del self._tools[name]
            self._schema_cache = None
            self._toolbox_schema_cache.clear()
            return True
        return False

    def get(self, name: str) -> RegisteredTool | None:
        """查询指定名称的工具

        Args:
            name: 工具名称

        Returns:
            工具对象，或 None 如果未注册
        """
        return self._tools.get(name)

    def get_all(self) -> dict[str, RegisteredTool]:
        """获取所有已注册的工具（只读快照）

        返回副本，防止外部意外修改内部状态。

        Returns:
            所有工具的字典副本
        """
        return dict(self._tools)

    def get_schemas(self) -> list[ChatCompletionToolParam]:
        """提取所有工具的 OpenAI schema 列表

        用于传递给 client.chat.completions.create() 的 tools 参数。
        LLM 根据这些 schema 理解可用工具及其参数。

        带有缓存：register / unregister 时失效。

        Returns:
            OpenAI SDK 兼容的工具 schema 数组
        """
        if self._schema_cache is None:
            self._schema_cache = [t.schema for t in self._tools.values()]
        return self._schema_cache

    def list(self) -> list[str]:
        """获取所有工具的名称列表

        Returns:
            工具名称数组，按注册顺序排列
        """
        return list(self._tools.keys())

    def get_schemas_by_toolboxes(
        self, ids: Sequence[str]
    ) -> builtins.list[ChatCompletionToolParam]:
        """按工具箱 ID 筛选工具的 schema 列表

        工作流程：
        1. Phase 1（规划阶段）：LLM 分析需求，返回 required_toolboxes 列表
        2. Phase 2（执行阶段）：调用此方法，只传入相关工具箱的工具给 LLM

        筛选规则：
        - 如果 ids 为空列表 → 返回全部工具（兜底策略）
        - 工具的 toolbox 字段在 id_set 中 → 包含
        - 工具的 toolbox 字段未设置（None）→ 始终包含（视为核心能力）

        带有缓存：register / unregister 时失效。

        Args:
            ids: 工具箱 ID 数组（如 ["file_read", "exec"]）

        Returns:
            匹配工具的 OpenAI schema 数组
        """
        if not ids:
            return self.get_schemas()
        cache_key = frozenset(ids)
        if cache_key not in self._toolbox_schema_cache:
            id_set = set(ids)
            self._toolbox_schema_cache[cache_key] = [
                t.schema for t in self._tools.values() if t.toolbox is None or t.toolbox in id_set
            ]
            while len(self._toolbox_schema_cache) > _TOOLBOX_SCHEMA_CACHE_MAX:
                self._toolbox_schema_cache.popitem(last=False)
        else:
            self._toolbox_schema_cache.move_to_end(cache_key)
        return self._toolbox_schema_cache[cache_key]

    def get_by_toolboxes(self, ids: Sequence[str]) -> dict[str, RegisteredTool]:
        """按工具箱 ID 筛选完整的工具对象

        与 get_schemas_by_toolboxes() 的区别：
        - get_schemas_by_toolboxes() → 只返回 schema（用于 LLM 调用）
        - get_by_toolboxes() → 返回完整工具对象（包含 handler，用于本地执行）

        Args:
            ids: 工具箱 ID 数组

        Returns:
            匹配工具的字典（名称 → 完整工具对象）
        """
        if not ids:
            return self.get_all()
        id_set = set(ids)
        return {
            name: tool
            for name, tool in self._tools.items()
            if tool.toolbox is None or tool.toolbox in id_set
        }


__all__ = ["DefaultToolRegistry"]
