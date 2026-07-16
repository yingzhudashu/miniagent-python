"""Immutable settings supplied to an Agent instance by the product layer."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class AgentSettings(Mapping[str, Any]):
    """Read-only Agent-owned view of the current application configuration."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        frozen = _freeze(values)
        if not isinstance(frozen, Mapping):  # pragma: no cover
            raise TypeError("AgentSettings requires a mapping")
        self._values = frozen

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get_path(self, path: str, default: Any = None) -> Any:
        current: Any = self._values
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def section(self, name: str) -> dict[str, Any]:
        value = self.get_path(name, {})
        return dict(value) if isinstance(value, Mapping) else {}


_CURRENT_SETTINGS: ContextVar[AgentSettings | None] = ContextVar(
    "miniagent_agent_settings", default=None
)


def _current() -> AgentSettings:
    return _CURRENT_SETTINGS.get() or AgentSettings({})


@contextmanager
def use_agent_settings(settings: AgentSettings):
    """Scope settings to one Agent call and all child async tasks."""
    token = _CURRENT_SETTINGS.set(settings)
    try:
        yield
    finally:
        _CURRENT_SETTINGS.reset(token)


def get_config(path: str, default: Any = None) -> Any:
    return _current().get_path(path, default)


def get_config_bool(path: str, default: bool = False) -> bool:
    value = _current().get_path(path, default)
    return value if isinstance(value, bool) else default


def get_config_section(name: str) -> dict[str, Any]:
    return _current().section(name)


__all__ = ["AgentSettings"]
