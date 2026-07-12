"""Engine — 模块化引擎包（进程内运行时编排）

拆分自 unified.py (890 行)。本包负责 **启动与关停、CLI 主循环、命令调度、飞书任务生命周期、
会话锁与欢迎界面**；与 ``miniagent.core`` 的边界：core 不包含 asyncio 主循环与 stdin 交互。

未在 ``__all__`` 中列出但仍常用的模块（按需直接 import）：

- ``command_dispatch``：``.`` 命令统一调度
- ``cli_state``：``CliLoopState`` TypedDict，与 ``run_runtime`` 状态字典对齐
- ``builtin_tools``：``register_builtin_tools``
- ``feishu_state``：飞书运行时状态，由 ``ApplicationContainer.feishu`` 持有

其它模块：``session_lock``、``thinking``、``engine``、``cli_commands``、``feishu_state``、
``init``、``main``、``cli_tui``、``cli_fallback``、``welcome``、``markdown_cli``、``clipboard``、``shutdown``
（见各文件模块文档）。

主架构与用户可见命令见 ``docs/ARCHITECTURE.md``、``docs/CLI.md``。
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "CliLoopState": "miniagent.engine.cli_state",
    "FeishuRuntime": "miniagent.engine.feishu_state",
    "ThinkingDisplay": "miniagent.engine.thinking",
    "UnifiedEngine": "miniagent.engine.engine",
    "cmd_help": "miniagent.engine.cli_commands",
    "cmd_queue_set": "miniagent.engine.cli_commands",
    "cmd_queue_status": "miniagent.engine.cli_commands",
    "cmd_session_create": "miniagent.engine.cli_commands",
    "cmd_session_delete": "miniagent.engine.cli_commands",
    "cmd_session_list": "miniagent.engine.cli_commands",
    "cmd_session_rename": "miniagent.engine.cli_commands",
    "cmd_session_switch": "miniagent.engine.cli_commands",
    "dispatch_command": "miniagent.engine.command_dispatch",
    "get_session_display": "miniagent.engine.welcome",
    "get_version": "miniagent.engine.welcome",
    "init_subsystems": "miniagent.engine.init",
    "is_session_locked": "miniagent.engine.session_lock",
    "print_welcome": "miniagent.engine.welcome",
    "release_session_lock": "miniagent.engine.session_lock",
    "run_cli_loop": "miniagent.engine.cli_tui",
    "run_runtime": "miniagent.engine.main",
    "shutdown_runtime": "miniagent.engine.shutdown",
    "try_lock_session": "miniagent.engine.session_lock",
    "try_lock_session_async": "miniagent.engine.session_lock",
}


def __getattr__(name: str) -> Any:
    """Load aggregate exports on first access without importing the whole runtime."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        value = getattr(importlib.import_module(module_name), name)
    except ImportError:
        if name != "ThinkingDisplay":
            raise
        value = None
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy aggregate names to discovery and documentation tools."""
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "try_lock_session",
    "try_lock_session_async",
    "release_session_lock",
    "is_session_locked",
    "ThinkingDisplay",
    "UnifiedEngine",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_session_delete",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "FeishuRuntime",
    "init_subsystems",
    "run_runtime",
    "run_cli_loop",
    "shutdown_runtime",
    "get_version",
    "get_session_display",
    "print_welcome",
    # CLI 状态类型
    "CliLoopState",
    # 命令调度主入口
    "dispatch_command",
]
