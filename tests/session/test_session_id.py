"""Tests for miniagent.assistant.utils.session_id."""

from __future__ import annotations

import doctest

import pytest

from miniagent.assistant.utils import session_id as session_id_module
from miniagent.assistant.utils.session_id import safe_session_id


class TestSafeSessionId:
    """Unit tests for safe_session_id."""

    @pytest.mark.parametrize(
        ("session_key", "expected"),
        [
            ("cli-session-1", "cli-session-1"),
            ("feishu:oc_abc123", "feishu_oc_abc123"),
            ("test/session", "test_session"),
            ("a/b\\c", "a_b_c"),
            ("user@example.com", "user_example_com"),
            ("..", "__"),
            ("a:b", "a_b"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_safe_session_id(self, session_key: str | None, expected: str) -> None:
        assert safe_session_id(session_key) == expected

    def test_unicode_replaced(self) -> None:
        assert safe_session_id("会话-1") == "__-1"

    def test_idempotent_for_already_safe_keys(self) -> None:
        key = "abc_123-xyz"
        assert safe_session_id(key) == key


def test_module_doctests() -> None:
    """Docstring examples in session_id.py must stay runnable."""
    failures, _tests = doctest.testmod(session_id_module, verbose=0)
    assert failures == 0
