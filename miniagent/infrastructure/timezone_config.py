"""进程级 IANA 时区 SSOT：Agent、工具与定时任务默认均由此解析。

**配置**：
- 从JSON配置加载默认值，环境变量可覆盖
"""

from __future__ import annotations

import os
from datetime import datetime

from miniagent.infrastructure.json_config import get_config

# 从JSON配置加载默认值
_DEFAULT_FALLBACK = get_config("timezone.default_fallback", "Asia/Shanghai")

_WEEKDAYS_ZH = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


def _validate_iana(name: str) -> str | None:
    """验证 IANA 时区名称有效性；无效时返回 None。"""
    raw = (name or "").strip()
    if not raw:
        return None
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(raw)
        return raw
    except Exception:
        return None


def process_timezone() -> str:
    """进程默认 IANA 时区（Agent、``get_time`` 等）。

    优先级：``MINIAGENT_TIMEZONE`` → ``TZ`` → ``Asia/Shanghai``。
    定时任务新建默认见 ``default_schedule_timezone()``（可单独 ``MINIAGENT_SCHEDULE_TIMEZONE``）。
    """
    for raw in (
        os.environ.get("MINIAGENT_TIMEZONE", "").strip(),
        os.environ.get("TZ", "").strip(),
        _DEFAULT_FALLBACK,
    ):
        ok = _validate_iana(raw)
        if ok:
            return ok
    # 理论上不可达（Asia/Shanghai 始终有效），保留以防极端环境 zoneinfo 缺失
    return "UTC"


def now_in_process_tz() -> datetime:
    """当前进程时区下的本地时间（aware）。"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(process_timezone()))


def format_process_local(epoch: float, *, tz_name: str | None = None) -> str:
    """将 unix 秒格式化为指定或进程时区下的本地时间字符串。"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo((tz_name or "").strip() or process_timezone())
    return datetime.fromtimestamp(float(epoch), tz=tz).strftime("%Y-%m-%d %H:%M %Z")


def format_agent_timezone_context() -> str:
    """注入 Agent system / 定时任务 prompt 的时区说明块。"""
    tz_name = process_timezone()
    now = now_in_process_tz()
    weekday = _WEEKDAYS_ZH[now.weekday()]
    local = f"{now.year}年{now.month}月{now.day}日{weekday} {now.strftime('%H:%M:%S')}"
    return (
        f"当前进程时区：{tz_name}；本地时间：{local}。"
        "涉及「今天/明天」、cron 墙钟或定时任务说明时，以该时区为准；"
        "磁盘上的 UTC 时间戳仅作存储。"
    )
