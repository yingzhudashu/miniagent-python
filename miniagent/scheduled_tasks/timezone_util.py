"""定时任务默认 IANA 时区（与 :mod:`miniagent.infrastructure.timezone_config` 对齐）。"""

from __future__ import annotations

import os

from miniagent.infrastructure.timezone_config import _validate_iana, process_timezone


def default_schedule_timezone() -> str:
    """未显式 ``--tz`` 时写入 ``tasks.json`` 的时区名。

    若设置 ``MINIAGENT_SCHEDULE_TIMEZONE`` 则仅调度默认使用该值（否则与 ``process_timezone()`` 相同）。
    """
    sched = os.environ.get("MINIAGENT_SCHEDULE_TIMEZONE", "").strip()
    if sched:
        ok = _validate_iana(sched)
        if ok:
            return ok
    return process_timezone()
