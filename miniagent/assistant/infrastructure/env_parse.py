"""环境变量解析：统一真值/假值集合与默认值读取。"""

from __future__ import annotations

import os

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
    "env_flag",
    "env_flag_strict",
    "env_choice",
]
