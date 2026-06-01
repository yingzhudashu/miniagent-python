"""定时任务默认 IANA 时区（与 :mod:`miniagent.infrastructure.timezone_config` 对齐）。"""

from __future__ import annotations

import os

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.timezone_config import _validate_iana, process_timezone


def default_schedule_timezone() -> str:
    """未显式 ``--tz`` 时写入 ``tasks.json`` 的时区名。

    优先级（从高到低）：
    1. MINIAGENT_SCHEDULE_TIMEZONE环境变量（特殊别名）
    2. MINIAGENT_SCHEDULED_TASKS_TIMEZONE环境变量（标准命名）
    3. JSON配置 scheduled_tasks.timezone
    4. process_timezone()（进程默认时区）
    """
    # 1. 检查MINIAGENT_SCHEDULE_TIMEZONE（特殊别名）
    raw = os.environ.get("MINIAGENT_SCHEDULE_TIMEZONE", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 2. 检查MINIAGENT_SCHEDULED_TASKS_TIMEZONE（标准命名）
    raw = os.environ.get("MINIAGENT_SCHEDULED_TASKS_TIMEZONE", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 3. 从JSON配置读取
    sched = get_config("scheduled_tasks.timezone", "")
    if sched:
        ok = _validate_iana(sched)
        if ok:
            return ok

    # 4. 回退到进程默认时区
    return process_timezone()
