"""将 ALL_TOOLS 注册到主 ToolRegistry（内置工具先于技能包加载）。

同名冲突策略：**内置优先**。技能包注册时使用 try/except，已占用名称则跳过。

环境变量收敛暴露面（自我优化 / 点命令 / 定时任务工具）的说明见 ``README`` 与 ``docs/SECURITY.md``。
"""

from __future__ import annotations

import os
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.tools import ALL_TOOLS
from miniagent.tools.cli_dispatch_tools import CLI_DOT_TOOL_NAMES
from miniagent.tools.schedule_tools import SCHEDULE_TOOL_NAMES
from miniagent.tools.self_opt import self_opt_tools

_logger = get_logger(__name__)

_SELF_OPT_NAMES = frozenset(self_opt_tools.keys())


def _self_opt_registration_enabled() -> bool:
    """默认注册自我优化工具；设为 0/false/off 则跳过（缩小暴露面）。"""
    v = os.environ.get("MINIAGENT_SELF_OPT_TOOLS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _cli_dot_tools_registration_enabled() -> bool:
    """默认注册 run_dot_command；设为 0/false/off 则跳过。"""
    v = os.environ.get("MINIAGENT_CLI_DOT_TOOLS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _schedule_tools_registration_enabled() -> bool:
    """默认注册 manage_scheduled_task；设为 0/false/off 则跳过。"""
    v = os.environ.get("MINIAGENT_SCHEDULE_TOOLS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def register_builtin_tools(registry: Any) -> int:
    """注册 ALL_TOOLS 中的内置工具（可按环境变量排除 self_opt）。

    Returns:
        成功注册的工具数量（不含已存在而跳过的条目）。
    """
    skip_self_opt = not _self_opt_registration_enabled()
    skip_cli_dot = not _cli_dot_tools_registration_enabled()
    skip_schedule = not _schedule_tools_registration_enabled()
    n = 0
    for name, tool in ALL_TOOLS.items():
        if skip_self_opt and name in _SELF_OPT_NAMES:
            continue
        if skip_cli_dot and name in CLI_DOT_TOOL_NAMES:
            continue
        if skip_schedule and name in SCHEDULE_TOOL_NAMES:
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
