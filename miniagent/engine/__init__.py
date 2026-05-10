"""Engine — 模块化引擎包（进程内运行时编排）

拆分自 unified.py (890 行)。本包负责 **启动与关停、CLI 主循环、命令调度、飞书任务生命周期、
会话锁与欢迎界面**；与 ``miniagent.core`` 的边界：core 不包含 asyncio 主循环与 stdin 交互。

未在 ``__all__`` 中列出但仍常用的模块（按需直接 import）：

- ``command_dispatch``：``.`` 命令统一调度
- ``cli_state``：``CliLoopState`` TypedDict，与 ``unified_main`` 状态字典对齐
- ``builtin_tools``：``register_builtin_tools``
- ``feishu_runtime``：对 ``feishu_state.FeishuRuntime`` 的兼容重导出

其它模块：``session_lock``、``thinking``、``engine``、``cli_commands``、``feishu_state``、
``init``、``main``、``welcome``（见各文件模块文档）。
"""

from miniagent.engine.session_lock import try_lock_session, release_session_lock, is_session_locked
from miniagent.engine.thinking import ThinkingDisplay
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.cli_commands import (
    cmd_session_list,
    cmd_session_switch,
    cmd_session_create,
    cmd_session_rename,
    cmd_queue_status,
    cmd_queue_set,
    cmd_help,
)
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.engine.init import init_subsystems
from miniagent.engine.main import unified_main, run_cli_loop
from miniagent.engine.welcome import get_version, get_session_display, print_welcome

__all__ = [
    "try_lock_session",
    "release_session_lock",
    "is_session_locked",
    "ThinkingDisplay",
    "UnifiedEngine",
    "cmd_session_list",
    "cmd_session_switch",
    "cmd_session_create",
    "cmd_session_rename",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "FeishuRuntime",
    "init_subsystems",
    "unified_main",
    "run_cli_loop",
    "get_version",
    "get_session_display",
    "print_welcome",
]
