"""将 ALL_TOOLS 注册到主 ToolRegistry（内置工具先于技能包加载）。

同名冲突策略：**内置优先**。技能包注册时使用 try/except，已占用名称则跳过。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.tools import ALL_TOOLS
from miniagent.tools.self_opt import self_opt_tools

_logger = get_logger(__name__)

_SELF_OPT_NAMES = frozenset(self_opt_tools.keys())


def _self_opt_registration_enabled() -> bool:
    """默认注册自我优化工具；设为 0/false/off 则跳过（缩小暴露面）。"""
    v = os.environ.get("MINIAGENT_SELF_OPT_TOOLS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def register_builtin_tools(registry: Any) -> int:
    """注册 ALL_TOOLS 中的内置工具（可按环境变量排除 self_opt）。

    Returns:
        成功注册的工具数量（不含已存在而跳过的条目）。
    """
    skip_self_opt = not _self_opt_registration_enabled()
    n = 0
    for name, tool in ALL_TOOLS.items():
        if skip_self_opt and name in _SELF_OPT_NAMES:
            continue
        try:
            registry.register(name, tool)
            n += 1
        except ValueError:
            _logger.debug(
                '注册表已有同名工具 "%s"，跳过内置定义（内置优先策略）',
                name,
            )
    return n


__all__ = ["register_builtin_tools"]
