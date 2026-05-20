"""``miniagent.infrastructure.env_parse`` 单元测试。"""

from __future__ import annotations

import pytest


def test_env_flag_default_and_truthy(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure.env_parse import env_flag

    monkeypatch.delenv("MINIAGENT_TEST_FLAG", raising=False)
    assert env_flag("MINIAGENT_TEST_FLAG", default=False) is False
    assert env_flag("MINIAGENT_TEST_FLAG", default=True) is True
    for v in ("1", "true", "YES", "on"):
        monkeypatch.setenv("MINIAGENT_TEST_FLAG", v)
        assert env_flag("MINIAGENT_TEST_FLAG", default=False) is True


def test_env_flag_falsy(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure.env_parse import env_flag

    for v in ("0", "false", "off", "no"):
        monkeypatch.setenv("MINIAGENT_TEST_FLAG", v)
        assert env_flag("MINIAGENT_TEST_FLAG", default=True) is False


def test_env_flag_unknown_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure.env_parse import env_flag

    monkeypatch.setenv("MINIAGENT_TEST_FLAG", "maybe")
    assert env_flag("MINIAGENT_TEST_FLAG", default=True) is True
    assert env_flag("MINIAGENT_TEST_FLAG", default=False) is False


def test_env_str_and_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure.env_parse import env_choice, env_str

    monkeypatch.delenv("MINIAGENT_TEST_STR", raising=False)
    assert env_str("MINIAGENT_TEST_STR", "reply") == "reply"
    monkeypatch.setenv("MINIAGENT_TEST_STR", "  create  ")
    assert env_str("MINIAGENT_TEST_STR", "reply") == "create"
    assert env_choice("MINIAGENT_TEST_STR", frozenset({"create", "reply"}), default="reply") == "create"
    monkeypatch.setenv("MINIAGENT_TEST_STR", "typo")
    assert env_choice("MINIAGENT_TEST_STR", frozenset({"create", "reply"}), default="reply") == "reply"


def test_env_flag_strict_unknown_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.infrastructure.env_parse import env_flag_strict

    monkeypatch.delenv("MINIAGENT_TEST_FLAG", raising=False)
    assert env_flag_strict("MINIAGENT_TEST_FLAG", default=True) is True
    monkeypatch.setenv("MINIAGENT_TEST_FLAG", "maybe")
    assert env_flag_strict("MINIAGENT_TEST_FLAG", default=True) is False


def test_env_str_legacy_fallback_and_warn_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    from miniagent.infrastructure.env_parse import (
        env_str_legacy,
        reset_env_legacy_warnings_for_tests,
    )

    reset_env_legacy_warnings_for_tests()
    monkeypatch.delenv("MINIAGENT_TEST_NEW", raising=False)
    monkeypatch.setenv("MINIAGENT_TEST_OLD", "legacy-val")
    with patch("miniagent.infrastructure.env_parse._logger") as mock_log:
        assert env_str_legacy("MINIAGENT_TEST_NEW", "MINIAGENT_TEST_OLD") == "legacy-val"
        assert env_str_legacy("MINIAGENT_TEST_NEW", "MINIAGENT_TEST_OLD") == "legacy-val"
        assert mock_log.warning.call_count == 1
