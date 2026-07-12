"""应用层使用的不可变配置读取契约。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any


class ConfigSnapshot(Mapping[str, Any]):
    """进程启动时冻结的配置树，防止业务代码意外原地修改。"""

    def __init__(self, values: Mapping[str, Any]) -> None:
        """递归复制并冻结映射和列表。"""
        self._values = MappingProxyType(_freeze_mapping(values))

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get_path(self, path: str, default: Any = None) -> Any:
        """按点路径读取冻结配置；路径不存在时返回默认值。"""
        current: Any = self._values
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current


def _freeze_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _freeze(value) for key, value in values.items()}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(_freeze_mapping(value))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


__all__ = ["ConfigSnapshot"]
