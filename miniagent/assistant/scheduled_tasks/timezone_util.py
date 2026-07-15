"""定时任务默认 IANA 时区（与 :mod:`miniagent.agent.timezone` 对齐）。"""

from __future__ import annotations

from miniagent.agent.timezone import _validate_iana, process_timezone
from miniagent.assistant.infrastructure.json_config import get_config


def default_schedule_timezone() -> str:
    """未显式 ``--tz`` 时写入 ``tasks.json`` 的时区名。"""
    sched = get_config("scheduled_tasks.timezone", "")
    if sched:
        ok = _validate_iana(sched)
        if ok:
            return ok
    return process_timezone()
