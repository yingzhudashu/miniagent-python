"""用户可配置的定时任务：持久化、进程内 asyncio 调度、经 ``message_queue`` 执行 Agent 回合。

与用户文档对应关系：README「定时任务」、``docs/ARCHITECTURE.md`` 子系统说明、
``docs/USER_GUIDE.md`` §9。飞书侧仅允许部分 ``.schedule`` 子命令。"""

from miniagent.assistant.scheduled_tasks.cron import validate_cron_expr
from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec, SessionSpec
from miniagent.assistant.scheduled_tasks.store import (
    load_tasks,
    save_tasks,
    save_tasks_async,
    tasks_file_path,
)
from miniagent.assistant.scheduled_tasks.ticker import start_scheduled_tasks_ticker
from miniagent.assistant.scheduled_tasks.timezone_util import default_schedule_timezone

__all__ = [
    "ScheduledTask",
    "ScheduleSpec",
    "SessionSpec",
    "load_tasks",
    "save_tasks",
    "save_tasks_async",
    "tasks_file_path",
    "start_scheduled_tasks_ticker",
    # cron 验证
    "validate_cron_expr",
    # 默认时区
    "default_schedule_timezone",
]
