"""cron 表达式校验与下次触发计算。"""

from __future__ import annotations

import pytest

from miniagent.assistant.scheduled_tasks.cron import (
    cron_next_run_epoch,
    normalize_cron_expr,
    validate_cron_expr,
)


def test_validate_cron_five_fields() -> None:
    assert validate_cron_expr("10 8 * * *") == "10 8 * * *"


def test_validate_rejects_fullwidth_star() -> None:
    with pytest.raises(ValueError, match="ASCII"):
        normalize_cron_expr("10 8 ＊ ＊ ＊")


def test_validate_rejects_wrong_field_count() -> None:
    with pytest.raises(ValueError, match="5 段"):
        normalize_cron_expr("10 8 * *")


def test_cron_next_run_shanghai_daily() -> None:
    # 仅验证下次触发时刻严格晚于锚点（具体墙钟由 croniter 计算）
    after = 1718406000.0
    nxt = cron_next_run_epoch("10 8 * * *", "Asia/Shanghai", after)
    assert nxt > after


def test_cron_next_run_advances_after_fire() -> None:
    after = 1_700_000_000.0
    n1 = cron_next_run_epoch("0 12 * * *", "UTC", after)
    n2 = cron_next_run_epoch("0 12 * * *", "UTC", n1)
    assert n2 > n1


def test_validate_cron_rejects_invalid_minute() -> None:
    with pytest.raises(ValueError, match="无效"):
        validate_cron_expr("60 0 * * *")


def test_cron_next_run_invalid_timezone() -> None:
    with pytest.raises(ValueError, match="无效时区"):
        cron_next_run_epoch("0 12 * * *", "Not/A/Timezone", 1_700_000_000.0)
