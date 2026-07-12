"""进程级 IANA 时区 SSOT：Agent、工具与定时任务默认均由此解析。

配置来源：包内 defaults / config.user.json（``timezone.default``、``timezone.default_fallback``）。
"""

from __future__ import annotations

import os
from datetime import datetime

from miniagent.infrastructure.json_config import get_config

_DEFAULT_FALLBACK = "Asia/Shanghai"

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
    """进程默认 IANA 时区（Agent、``get_time`` 等）。"""
    raw = get_config("timezone.default", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    raw = os.environ.get("TZ", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    fallback = get_config("timezone.default_fallback", _DEFAULT_FALLBACK)
    ok = _validate_iana(str(fallback or ""))
    if ok:
        return ok
    return _DEFAULT_FALLBACK


def now_in_process_tz() -> datetime:
    """当前时刻，带进程默认 IANA 时区（见 :func:`process_timezone`）。"""
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(process_timezone()))


def format_process_local(epoch: float, *, tz_name: str | None = None) -> str:
    """将 Unix 时间戳格式化为本地墙钟字符串（默认进程时区）。"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo((tz_name or "").strip() or process_timezone())
    return datetime.fromtimestamp(float(epoch), tz=tz).strftime("%Y-%m-%d %H:%M %Z")


def format_agent_timezone_rule_context() -> str:
    """返回不含当前时间戳的稳定时区规则。

    执行阶段的 stable system prompt 会调用本函数：它只说明进程采用哪个 IANA 时区、
    如何解释「今天/明天」与 cron 墙钟时间，不包含秒级当前时间。当前具体时间由
    ``format_agent_timezone_context`` 生成，并放入每轮动态 user context，避免 stable
    system prefix 因时间变化而失去 prompt cache 命中机会。
    """
    tz_name = process_timezone()
    return (
        f"当前进程时区：{tz_name}。"
        "涉及「今天/明天」、cron 墙钟或定时任务说明时，以该时区为准；"
        "磁盘上的 UTC 时间戳仅作存储。当前具体时间由本轮用户上下文提供。"
    )


def format_agent_timezone_context() -> str:
    """返回含当前本地时间的动态时区上下文（注入每轮 user context）。"""
    tz_name = process_timezone()
    now = now_in_process_tz()
    weekday = _WEEKDAYS_ZH[now.weekday()]
    local = f"{now.year}年{now.month}月{now.day}日{weekday} {now.strftime('%H:%M:%S')}"
    return (
        f"当前进程时区：{tz_name}；本地时间：{local}。"
        "涉及「今天/明天」、cron 墙钟或定时任务说明时，以该时区为准；"
        "磁盘上的 UTC 时间戳仅作存储。"
    )
