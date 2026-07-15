"""Runtime settings port used by the reusable agent layer.

The agent package deliberately does not know where configuration is stored.
The assistant composition root installs its getters during bootstrap; importing
the agent package on its own remains side-effect free and uses caller defaults.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

ConfigGetter = Callable[[str, Any], Any]
BoolGetter = Callable[[str, bool], bool]
SectionGetter = Callable[[str], Mapping[str, Any]]


def _default_getter(_path: str, default: Any = None) -> Any:
    return default


def _default_bool_getter(_path: str, default: bool = False) -> bool:
    return default


def _default_section_getter(_name: str) -> Mapping[str, Any]:
    return {}


_getter: ConfigGetter = _default_getter
_bool_getter: BoolGetter = _default_bool_getter
_section_getter: SectionGetter = _default_section_getter
_user_section_getter: SectionGetter = _default_section_getter


def configure_agent_settings(
    *,
    getter: ConfigGetter,
    bool_getter: BoolGetter,
    section_getter: SectionGetter,
    user_section_getter: SectionGetter,
) -> None:
    """Install Assistant-owned configuration readers for future agent calls."""
    global _getter, _bool_getter, _section_getter, _user_section_getter
    _getter = getter
    _bool_getter = bool_getter
    _section_getter = section_getter
    _user_section_getter = user_section_getter


def reset_agent_settings() -> None:
    """Restore standalone defaults, primarily for isolated import tests."""
    configure_agent_settings(
        getter=_default_getter,
        bool_getter=_default_bool_getter,
        section_getter=_default_section_getter,
        user_section_getter=_default_section_getter,
    )


def get_config(path: str, default: Any = None) -> Any:
    return _getter(path, default)


def get_config_bool(path: str, default: bool = False) -> bool:
    return _bool_getter(path, default)


def get_config_section(name: str) -> dict[str, Any]:
    return dict(_section_getter(name))


def get_user_config_section(name: str) -> dict[str, Any]:
    return dict(_user_section_getter(name))


__all__ = [
    "configure_agent_settings",
    "get_config",
    "get_config_bool",
    "get_config_section",
    "get_user_config_section",
    "reset_agent_settings",
]
