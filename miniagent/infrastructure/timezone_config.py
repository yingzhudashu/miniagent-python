"""进程级 IANA 时区 SSOT：Agent、工具与定时任务默认均由此解析。

**配置**：
- 从JSON配置加载默认值（timezone.default）
- MINIAGENT_TIMEZONE环境变量覆盖（特殊别名，兼容常见用法）
- TZ环境变量作为最终回退
"""

from __future__ import annotations

import os
from datetime import datetime

from miniagent.infrastructure.json_config import get_config

# 默认时区（从JSON配置读取）
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

    优先级（从高到低）：
    1. MINIAGENT_TIMEZONE环境变量（特殊别名）
    2. MINIAGENT_TIMEZONE_DEFAULT环境变量（标准命名）
    3. JSON配置 timezone.default
    4. TZ环境变量（系统标准）
    5. 默认值 Asia/Shanghai
    """
    # 1. 检查MINIAGENT_TIMEZONE（特殊别名）
    raw = os.environ.get("MINIAGENT_TIMEZONE", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 2. 检查MINIAGENT_TIMEZONE_DEFAULT（标准命名）
    raw = os.environ.get("MINIAGENT_TIMEZONE_DEFAULT", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 3. 从JSON配置读取
    raw = get_config("timezone.default", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 4. 检查TZ环境变量
    raw = os.environ.get("TZ", "")
    ok = _validate_iana(raw)
    if ok:
        return ok

    # 5. 默认回退
    return "Asia/Shanghai"


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
