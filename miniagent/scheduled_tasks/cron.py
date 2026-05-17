"""标准 5 段 Unix cron 表达式校验与 ``next_run_at`` 计算。"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from croniter import croniter

_ASCII_CRON_RE = re.compile(r"^[\d*,/\-\w\s]+$", re.IGNORECASE)


def normalize_cron_expr(expr: str) -> str:
    """NFKC 规范化并折叠空白；拒绝非 ASCII cron 字符（如全角 ``＊``）。"""
    stripped = (expr or "").strip()
    if any(ord(c) > 127 for c in stripped):
        raise ValueError(
            "cron 表达式须为 ASCII（分 时 日 月 周），请使用半角 * 而非全角 ＊ 等字符"
        )
    raw = unicodedata.normalize("NFKC", stripped)
    collapsed = " ".join(raw.split())
    if not collapsed:
        raise ValueError("cron 表达式不能为空")
    if not _ASCII_CRON_RE.match(collapsed):
        raise ValueError(
            "cron 表达式须为 ASCII（分 时 日 月 周），请使用半角 * 而非全角 ＊ 等字符"
        )
    parts = collapsed.split()
    if len(parts) != 5:
        raise ValueError(f"cron 须为 5 段（分 时 日 月 周），当前为 {len(parts)} 段")
    return collapsed


def validate_cron_expr(expr: str) -> str:
    """校验并返回规范化后的 cron 表达式。"""
    normalized = normalize_cron_expr(expr)
    if not croniter.is_valid(normalized):
        raise ValueError(f"无效的 cron 表达式: {normalized!r}")
    return normalized


def cron_next_run_epoch(expr: str, timezone: str, after_ts: float) -> float:
    """返回严格晚于 ``after_ts`` 的下一触发时刻（unix 秒，按 ``timezone`` 墙钟）。"""
    from zoneinfo import ZoneInfo

    normalized = validate_cron_expr(expr)
    try:
        tz = ZoneInfo((timezone or "UTC").strip() or "UTC")
    except Exception as e:
        raise ValueError(f"无效时区 {timezone!r}") from e
    base = datetime.fromtimestamp(after_ts, tz=tz)
    itr = croniter(normalized, base)
    nxt = itr.get_next(datetime)
    if nxt.tzinfo is None:
        nxt = nxt.replace(tzinfo=tz)
    ts = nxt.timestamp()
    if ts <= after_ts:
        nxt2 = itr.get_next(datetime)
        if nxt2.tzinfo is None:
            nxt2 = nxt2.replace(tzinfo=tz)
        ts = nxt2.timestamp()
    return float(ts)
