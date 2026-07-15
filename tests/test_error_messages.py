"""Tests for miniagent.agent.types.error_messages."""

from __future__ import annotations

import doctest

import pytest

from miniagent.agent.types import error_messages as error_messages_module
from miniagent.agent.types.error_messages import (
    COMMAND_BLOCKED_DANGER,
    FILE_NOT_FOUND_WITH_PATH,
    FILE_WRITTEN,
    SCHEDULE_ONCE_PARSE_FAILED,
    SCHEDULE_TASK_ADDED,
    SCHEDULE_TASK_ID_MISSING,
    TEXT_NOT_FOUND,
    format_message,
)
from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX


class TestFormatMessage:
    """Unit tests for format_message."""

    def test_single_placeholder(self) -> None:
        assert format_message(FILE_NOT_FOUND_WITH_PATH, path="/test.txt") == (
            f"{ERROR_PREFIX} 文件不存在: /test.txt"
        )

    def test_multiple_placeholders(self) -> None:
        msg = format_message(FILE_WRITTEN, path="/out.txt", size=128)
        assert msg == f"{SUCCESS_PREFIX} 已写入 /out.txt (128 字节)"

    def test_named_action_placeholder(self) -> None:
        msg = format_message(SCHEDULE_TASK_ID_MISSING, action="update")
        assert msg == f"{ERROR_PREFIX} update 需要 task_id"

    def test_quoted_pattern_in_template(self) -> None:
        msg = format_message(COMMAND_BLOCKED_DANGER, pattern="rm -rf")
        assert '"rm -rf"' in msg

    def test_text_not_found_literal_ellipsis(self) -> None:
        msg = format_message(TEXT_NOT_FOUND, text="hello")
        assert "hello" in msg
        assert "..." in msg

    def test_missing_kwargs_leaves_placeholder(self) -> None:
        assert format_message(FILE_NOT_FOUND_WITH_PATH) == FILE_NOT_FOUND_WITH_PATH

    def test_extra_kwargs_ignored(self) -> None:
        msg = format_message(FILE_NOT_FOUND_WITH_PATH, path="/a", unused="x")
        assert msg == f"{ERROR_PREFIX} 文件不存在: /a"

    def test_bool_and_float_coerced_to_str(self) -> None:
        msg = format_message(SCHEDULE_TASK_ADDED, kind="cron", tid="t1")
        assert msg == f"{SUCCESS_PREFIX} 已添加 cron 任务 t1"

    @pytest.mark.parametrize(
        "name",
        sorted(
            name
            for name in error_messages_module.__all__
            if name != "format_message" and name.isupper()
        ),
    )
    def test_exported_constants_are_nonempty_strings(self, name: str) -> None:
        value = getattr(error_messages_module, name)
        assert isinstance(value, str)
        assert value.strip()


def test_schedule_once_parse_failed_constant_name_matches_message() -> None:
    assert "once_iso" in SCHEDULE_ONCE_PARSE_FAILED


def test_module_doctests() -> None:
    """Docstring examples in error_messages.py must stay runnable."""
    failures, _tests = doctest.testmod(error_messages_module, verbose=0)
    assert failures == 0
