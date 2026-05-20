"""环境变量解析：统一真值/假值集合与默认值读取。"""

from __future__ import annotations

import os

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)
_warned_legacy_env: set[str] = set()

TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"0", "false", "no", "off"})


def env_str(name: str, default: str = "") -> str:
    """读取字符串；未设置或仅空白时返回 *default*。"""
    v = os.environ.get(name)
    if v is None:
        return default
    s = v.strip()
    return s if s else default


def env_flag(name: str, *, default: bool = False) -> bool:
    """读取布尔开关。未设置时 *default*；显式真/假值集合；其它非空字符串按 *default*。"""
    v = os.environ.get(name)
    if v is None:
        return default
    s = v.strip().lower()
    if not s:
        return default
    if s in TRUTHY:
        return True
    if s in FALSY:
        return False
    return default


def env_flag_strict(name: str, *, default: bool = False) -> bool:
    """读取布尔开关；无法识别的非空字符串一律视为 **关**（与 ``MINIAGENT_FEISHU_TOOLS`` 拼写容错一致）。

    用于默认开启的开关，避免 ``maybe`` 等误拼写仍保持开启。
    """
    v = os.environ.get(name)
    if v is None:
        return default
    s = v.strip().lower()
    if not s:
        return default
    if s in TRUTHY:
        return True
    if s in FALSY:
        return False
    return False


def env_str_legacy(
    name: str,
    legacy_name: str,
    *,
    default: str = "",
    deprecate_msg: str | None = None,
) -> str:
    """读取 *name*；为空时回退 *legacy_name* 并打一次弃用 WARNING。"""
    v = env_str(name, default)
    if v:
        return v
    leg = env_str(legacy_name, "")
    if not leg:
        return default if default else ""
    if legacy_name not in _warned_legacy_env:
        _warned_legacy_env.add(legacy_name)
        if deprecate_msg:
            _logger.warning("%s", deprecate_msg)
        else:
            _logger.warning("环境变量 %s 已弃用，请改用 %s。", legacy_name, name)
    return leg


def reset_env_legacy_warnings_for_tests() -> None:
    """单测用：清空弃用 env 警告去重集合。"""
    _warned_legacy_env.clear()


def env_choice(name: str, choices: frozenset[str] | set[str], *, default: str) -> str:
    """读取枚举；未设置或不在 *choices* 内时返回 *default*。"""
    v = env_str(name, default)
    if v in choices:
        return v
    return default


__all__ = [
    "TRUTHY",
    "FALSY",
    "env_str",
    "env_str_legacy",
    "env_flag",
    "env_flag_strict",
    "env_choice",
    "reset_env_legacy_warnings_for_tests",
]
