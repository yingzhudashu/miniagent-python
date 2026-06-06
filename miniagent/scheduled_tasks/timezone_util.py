"""定时任务默认 IANA 时区（与 :mod:`miniagent.infrastructure.timezone_config` 对齐）。"""

from __future__ import annotations

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.timezone_config import _validate_iana, process_timezone


def default_schedule_timezone() -> str:
    """未显式 ``--tz`` 时写入 ``tasks.json`` 的时区名。"""
    sched = get_config("scheduled_tasks.timezone", "")
    if sched:
        ok = _validate_iana(sched)
        if ok:
            return ok
    return process_timezone()
