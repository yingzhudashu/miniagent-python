"""Engine — 模块化引擎包（进程内运行时编排）

拆分自 unified.py (890 行)。本包负责 **启动与关停、CLI 主循环、命令调度、飞书任务生命周期、
会话锁与欢迎界面**；与 ``miniagent.core`` 的边界：core 不包含 asyncio 主循环与 stdin 交互。

未在 ``__all__`` 中列出但仍常用的模块（按需直接 import）：

- ``command_dispatch``：``.`` 命令统一调度
- ``cli_state``：``CliLoopState`` TypedDict，与 ``unified_main`` 状态字典对齐
- ``builtin_tools``：``register_builtin_tools``
- ``feishu_state``：飞书运行时状态，由 ``RuntimeContext.feishu`` 持有

其它模块：``session_lock``、``thinking``、``engine``、``cli_commands``、``feishu_state``、
``init``、``main``、``welcome``、``markdown_cli``、``clipboard``、``shutdown``
（见各文件模块文档）。

主架构与用户可见命令见 ``docs/ARCHITECTURE.md``、``docs/CLI.md``。
"""

from miniagent.engine.cli_commands import (
    cmd_help,
    cmd_queue_set,
    cmd_queue_status,
    cmd_session_create,
    cmd_session_delete,
    cmd_session_list,
    cmd_session_rename,
    cmd_session_switch,
)
from miniagent.engine.engine import UnifiedEngine
from miniagent.engine.feishu_state import FeishuRuntime
from miniagent.engine.init import init_subsystems
from miniagent.engine.main import run_cli_loop, unified_main
from miniagent.engine.session_lock import is_session_locked, release_session_lock, try_lock_session
from miniagent.engine.shutdown import shutdown_runtime
from miniagent.engine.welcome import get_session_display, get_version, print_welcome

# ThinkingDisplay 需要 prompt_toolkit（cli extra），未安装时设为 None
try:
    from miniagent.engine.thinking import ThinkingDisplay
except ImportError:
    ThinkingDisplay = None  # type: ignore[misc,assignment]

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
    "cmd_session_delete",
    "cmd_queue_status",
    "cmd_queue_set",
    "cmd_help",
    "FeishuRuntime",
    "init_subsystems",
    "unified_main",
    "run_cli_loop",
    "shutdown_runtime",
    "get_version",
    "get_session_display",
    "print_welcome",
]
