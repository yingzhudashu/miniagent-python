"""Tests for miniagent.engine.session_continue."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from miniagent.bootstrap.application import ApplicationContainer
from miniagent.engine.session_continue import persist_cli_session_state, save_cli_session_state


class _FakeSessionManager:
    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = sessions

    def list_all_sessions_with_info(self) -> list[dict]:
        return list(self._sessions)


def test_persist_saves_matching_session() -> None:
    router = MagicMock()
    sm = _FakeSessionManager([{"id": "work-a", "number": 2, "title": "Work A"}])

    assert persist_cli_session_state(sm, "work-a", router) is True
    router.save_cli_session_state.assert_called_once_with("work-a", 2, "Work A")


@pytest.mark.parametrize(
    ("session_id", "session_manager", "use_router"),
    [
        ("", _FakeSessionManager([]), True),
        ("work-a", None, True),
        ("work-a", _FakeSessionManager([]), False),
    ],
)
def test_persist_skips_invalid_inputs(
    session_id: str,
    session_manager: _FakeSessionManager | None,
    use_router: bool,
) -> None:
    router = MagicMock()
    channel_router = router if use_router else None
    assert persist_cli_session_state(session_manager, session_id, channel_router) is False
    router.save_cli_session_state.assert_not_called()


def test_persist_skips_when_session_not_in_list() -> None:
    router = MagicMock()
    sm = _FakeSessionManager([{"id": "default", "number": 1, "title": "Default"}])

    assert persist_cli_session_state(sm, "missing", router) is False
    router.save_cli_session_state.assert_not_called()


def test_persist_returns_false_on_router_error(caplog: pytest.LogCaptureFixture) -> None:
    router = MagicMock()
    router.save_cli_session_state.side_effect = OSError("disk full")
    sm = _FakeSessionManager([{"id": "work-a", "number": 2, "title": "Work A"}])

    with caplog.at_level("DEBUG"):
        assert persist_cli_session_state(sm, "work-a", router) is False

    assert "保存 CLI 会话状态失败" in caplog.text


def test_persist_can_suppress_error_logging(caplog: pytest.LogCaptureFixture) -> None:
    router = MagicMock()
    router.save_cli_session_state.side_effect = RuntimeError("boom")
    sm = _FakeSessionManager([{"id": "work-a", "number": 1, "title": "X"}])

    with caplog.at_level("DEBUG"):
        assert (
            persist_cli_session_state(sm, "work-a", router, log_errors=False) is False
        )

    assert caplog.text == ""


def test_save_cli_session_state_delegates_to_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    def _fake_persist(session_manager, session_id, channel_router, *, log_errors=True):
        calls.append((session_manager, session_id, channel_router, log_errors))
        return True

    monkeypatch.setattr(
        "miniagent.engine.session_continue.persist_cli_session_state",
        _fake_persist,
    )
    sm = object()
    state = {"active_session_id": "foo", "session_manager": sm}
    ctx = MagicMock(spec=ApplicationContainer)
    ctx.channel_router = MagicMock()

    save_cli_session_state(ctx, state)  # type: ignore[arg-type]

    assert calls == [(sm, "foo", ctx.channel_router, True)]
