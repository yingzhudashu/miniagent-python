"""Default in-memory registry for Agent tool definitions."""

from __future__ import annotations

import builtins
import collections
from collections.abc import Sequence
from typing import Any

from miniagent.agent.types.tool import RegisteredTool, ToolDefinition

ChatCompletionToolParam = dict[str, Any]
_TOOLBOX_SCHEMA_CACHE_MAX = 128


class DefaultToolRegistry:
    """Register tools and cache provider schemas by selected toolbox."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._schema_cache: list[ChatCompletionToolParam] | None = None
        self._toolbox_schema_cache: collections.OrderedDict[
            frozenset[str], list[ChatCompletionToolParam]
        ] = collections.OrderedDict()

    def register(self, name: str, tool: ToolDefinition) -> None:
        if not name.strip():
            raise ValueError("tool name must not be empty")
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
        self._invalidate()

    def unregister(self, name: str) -> bool:
        if name not in self._tools:
            return False
        del self._tools[name]
        self._invalidate()
        return True

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def get_all(self) -> dict[str, RegisteredTool]:
        return dict(self._tools)

    def get_schemas(self) -> builtins.list[ChatCompletionToolParam]:
        if self._schema_cache is None:
            self._schema_cache = [tool.schema for tool in self._tools.values()]
        return self._schema_cache

    def list(self) -> builtins.list[str]:
        return builtins.list(self._tools)

    def get_schemas_by_toolboxes(
        self, ids: Sequence[str]
    ) -> builtins.list[ChatCompletionToolParam]:
        if not ids:
            return self.get_schemas()
        key = frozenset(ids)
        cached = self._toolbox_schema_cache.get(key)
        if cached is not None:
            self._toolbox_schema_cache.move_to_end(key)
            return cached
        selected = [
            tool.schema
            for tool in self._tools.values()
            if tool.toolbox is None or tool.toolbox in key
        ]
        self._toolbox_schema_cache[key] = selected
        while len(self._toolbox_schema_cache) > _TOOLBOX_SCHEMA_CACHE_MAX:
            self._toolbox_schema_cache.popitem(last=False)
        return selected

    def get_by_toolboxes(self, ids: Sequence[str]) -> dict[str, RegisteredTool]:
        if not ids:
            return self.get_all()
        selected = frozenset(ids)
        return {
            name: tool
            for name, tool in self._tools.items()
            if tool.toolbox is None or tool.toolbox in selected
        }

    def _invalidate(self) -> None:
        self._schema_cache = None
        self._toolbox_schema_cache.clear()


__all__ = ["DefaultToolRegistry"]
