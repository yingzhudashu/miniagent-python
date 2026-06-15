"""Tests for --continue / --session startup session resolution."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from miniagent.engine.init import _init_default_session
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.message_queue import MessageQueueManager
from miniagent.runtime.context import RuntimeContext
from tests.config_helpers import install_test_config


class _FakeSessionManager:
    def __init__(self, session_ids: list[str]) -> None:
        self._session_ids = session_ids
        self.created: list[str] = []

    def list_all_sessions_with_info(self) -> list[dict]:
        return [{"session_id": sid} for sid in self._session_ids]

    def get_or_create(self, session_id: str, _options: object) -> None:
        self.created.append(session_id)


class _RealFormatFakeSessionManager:
    """模拟 DefaultSessionManager.list_all_sessions_with_info 的真实字段。"""

    def __init__(self, sessions: list[dict]) -> None:
        self._sessions = sessions
        self.created: list[str] = []

    def list_all_sessions_with_info(self) -> list[dict]:
        return list(self._sessions)

    def get_or_create(self, session_id: str, _options: object) -> None:
        self.created.append(session_id)


class _FakeChannelRouter:
    def __init__(
        self,
        last_state: dict | None = None,
        *,
        primary: str | None = None,
        cli_binding: str | None = None,
    ) -> None:
        self._last_state = last_state or {}
        self.bound: list[tuple[str, str]] = []
        self.primary = primary
        self._cli_binding = cli_binding

    def load_cli_session_state(self) -> dict:
        return dict(self._last_state)

    def resolve(self, channel_id: str) -> str:
        if channel_id == "__cli__" and self._cli_binding:
            return self._cli_binding
        return channel_id

    def bind(self, channel_id: str, session_id: str) -> str:
        self.bound.append((channel_id, session_id))
        return session_id

    def set_primary(self, session_id: str) -> None:
        self.primary = session_id


@pytest.fixture(autouse=True)
def _always_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.engine.session_lock.try_lock_session",
        lambda _sid: (True, ""),
    )


@pytest.fixture
def _no_continue_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miniagent.engine.init.get_config",
        lambda key, default=None: default,
    )


def test_session_info_helpers() -> None:
    from miniagent.session.manager import session_info_id, session_info_number

    assert session_info_id({"id": "foo"}) == "foo"
    assert session_info_id({"session_id": "bar"}) == "bar"
    assert session_info_id({"id": "foo", "session_id": "bar"}) == "foo"
    assert session_info_number({"number": 3}) == 3
    assert session_info_number({"session_number": 5}) == 5
    assert session_info_number({}) == 0


def test_continue_resolves_with_real_list_format(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _RealFormatFakeSessionManager(
        [
            {"id": "default", "number": 1, "title": "Default"},
            {"id": "work-a", "number": 2, "title": "Work A"},
        ]
    )
    router = _FakeChannelRouter({"last_cli_session": "work-a"})
    sid = _init_default_session(sm, router)
    assert sid == "work-a"
    assert router.primary == "work-a"


def test_shutdown_saves_last_cli_with_real_list_format(tmp_path) -> None:
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.engine.shutdown import shutdown_runtime

    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    router = ChannelRouter()
    mq = MessageQueueManager()
    ctx = RuntimeContext(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=router,
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=MagicMock(),
        memory_context=MagicMock(),
        openai_client=None,
    )
    sm = _RealFormatFakeSessionManager(
        [{"id": "work-a", "number": 2, "title": "Work A"}]
    )
    state = {"active_session_id": "work-a", "session_manager": sm}

    async def _run() -> None:
        await shutdown_runtime(
            ctx,
            state,  # type: ignore[arg-type]
            reason="test_save_continue_real_format",
            abort_message_queues=False,
            release_cli_session_lock=False,
            call_unregister=False,
        )

    asyncio.run(_run())

    router2 = ChannelRouter()
    assert router2.load() is True
    saved = router2.load_cli_session_state()
    assert saved.get("last_cli_session") == "work-a"
    assert saved.get("last_cli_session_number") == 2
    assert saved.get("last_cli_session_title") == "Work A"


def test_continue_env_restores_last_session(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default", "work-a"])
    router = _FakeChannelRouter({"last_cli_session": "work-a"})
    sid = _init_default_session(sm, router)
    assert sid == "work-a"
    assert router.primary == "work-a"
    assert ("__cli__", "work-a") in router.bound


def test_continue_default_without_env(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.delenv("MINIAGENT_CONTINUE_SESSION", raising=False)
    sm = _FakeSessionManager(["default"])
    router = _FakeChannelRouter({"last_cli_session": "work-a"})
    sid = _init_default_session(sm, router)
    assert sid == "default"


def test_continue_config_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _cfg(key: str, default=None):
        if key == "session.continue_mode":
            return True
        return default

    monkeypatch.setattr("miniagent.engine.init.get_config", _cfg)
    monkeypatch.delenv("MINIAGENT_CONTINUE_SESSION", raising=False)
    sm = _FakeSessionManager(["default", "saved"])
    router = _FakeChannelRouter({"last_cli_session": "saved"})
    sid = _init_default_session(sm, router)
    assert sid == "saved"


def test_continue_deleted_session_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default"])
    router = _FakeChannelRouter({"last_cli_session": "gone"})
    sid = _init_default_session(sm, router)
    assert sid == "default"


def test_continue_fallback_to_primary_when_last_cli_null(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default", "foo"])
    router = _FakeChannelRouter({}, primary="foo")
    sid = _init_default_session(sm, router)
    assert sid == "foo"


def test_continue_fallback_to_cli_binding_when_last_cli_null(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default", "bar"])
    router = _FakeChannelRouter({}, cli_binding="bar")
    sid = _init_default_session(sm, router)
    assert sid == "bar"


def test_explicit_session_name_overrides_continue(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default", "last", "explicit"])
    router = _FakeChannelRouter({"last_cli_session": "last"})
    sid = _init_default_session(sm, router, session_name="explicit")
    assert sid == "explicit"


def test_session_env_via_init_subsystems_path(
    monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    monkeypatch.setenv("MINIAGENT_SESSION_NAME", "my-project")
    monkeypatch.delenv("MINIAGENT_CONTINUE_SESSION", raising=False)
    sm = _FakeSessionManager(["default", "my-project"])
    router = _FakeChannelRouter({"last_cli_session": "other"})
    sid = _init_default_session(sm, router, session_name="my-project")
    assert sid == "my-project"


def test_continue_restores_after_router_load_from_disk(
    tmp_path, monkeypatch: pytest.MonkeyPatch, _no_continue_config: None
) -> None:
    """模拟重启：磁盘上的 last_cli_session 须在 router.load() 后才可被 --continue 读取。"""
    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    router1 = ChannelRouter()
    router1.save_cli_session_state("work-a", 2, "Work A")

    router2 = ChannelRouter()
    assert router2.load_cli_session_state() == {}
    assert router2.load() is True

    monkeypatch.setenv("MINIAGENT_CONTINUE_SESSION", "1")
    sm = _FakeSessionManager(["default", "work-a"])
    sid = _init_default_session(sm, router2)
    assert sid == "work-a"


def test_shutdown_runtime_saves_last_cli_session(tmp_path) -> None:
    from miniagent.engine.feishu_state import FeishuRuntime
    from miniagent.engine.shutdown import shutdown_runtime

    install_test_config(tmp_path, {"paths": {"state_dir": str(tmp_path)}})
    router = ChannelRouter()
    mq = MessageQueueManager()
    ctx = RuntimeContext(
        registry=MagicMock(),
        monitor=MagicMock(),
        skill_registry=MagicMock(),
        clawhub=MagicMock(),
        engine=MagicMock(),
        channel_router=router,
        message_queue=mq,
        feishu=FeishuRuntime(mq),
        memory_store=MagicMock(),
        activity_log=MagicMock(),
        keyword_index=MagicMock(),
        memory_context=MagicMock(),
        openai_client=None,
    )
    sm = _FakeSessionManager(["work-a"])
    state = {"active_session_id": "work-a", "session_manager": sm}

    async def _run() -> None:
        await shutdown_runtime(
            ctx,
            state,  # type: ignore[arg-type]
            reason="test_save_continue",
            abort_message_queues=False,
            release_cli_session_lock=False,
            call_unregister=False,
        )

    asyncio.run(_run())

    router2 = ChannelRouter()
    assert router2.load() is True
    saved = router2.load_cli_session_state()
    assert saved.get("last_cli_session") == "work-a"


def test_consume_session_arg_sets_env_and_strips_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from miniagent.__main__ import _consume_session_arg

    monkeypatch.setattr(sys, "argv", ["-m", "miniagent", "--session", "foo", "--feishu"])
    monkeypatch.delenv("MINIAGENT_SESSION_NAME", raising=False)
    _consume_session_arg()
    import os

    assert os.environ.get("MINIAGENT_SESSION_NAME") == "foo"
    assert sys.argv == ["-m", "miniagent", "--feishu"]


def test_consume_session_arg_missing_value_exits() -> None:
    import sys

    from miniagent.__main__ import _consume_session_arg

    with patch.object(sys, "argv", ["-m", "miniagent", "--session"]):
        with pytest.raises(SystemExit) as exc:
            _consume_session_arg()
        assert exc.value.code == 2
