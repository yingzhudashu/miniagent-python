"""parallel_config 与 MessageQueue 并行模式配置测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from miniagent.engine.parallel_config import (
    configure_message_queue_for_parallel,
    resolve_active_session_key,
)
from miniagent.infrastructure.channel_router import ChannelRouter
from miniagent.infrastructure.json_config import get_config_bool
from miniagent.infrastructure.message_queue import MessageQueueManager


def test_configure_parallel_disables_cross_queue_serial() -> None:
    mq = MessageQueueManager()
    with patch(
        "miniagent.engine.parallel_config.get_config_bool",
        side_effect=lambda key, default=False: True if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is False
    assert mq.exec_lock is None


def test_configure_serial_enables_exec_lock() -> None:
    mq = MessageQueueManager()
    with patch(
        "miniagent.engine.parallel_config.get_config_bool",
        side_effect=lambda key, default=False: False if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is True
    assert mq.exec_lock is not None


def test_configure_parallel_clears_existing_exec_lock() -> None:
    mq = MessageQueueManager()
    mq.ensure_exec_lock()
    with patch(
        "miniagent.engine.parallel_config.get_config_bool",
        side_effect=lambda key, default=False: True if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is False
    assert mq.exec_lock is None


def test_configure_serial_string_false_treated_as_off() -> None:
    mq = MessageQueueManager()
    with patch(
        "miniagent.infrastructure.json_config.get_config",
        side_effect=lambda key, default=None: "false" if key == "agent.parallel_sessions" else default,
    ):
        configure_message_queue_for_parallel(mq)
    assert mq.cross_queue_serial is True
    assert mq.exec_lock is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("false", False),
        ("maybe", True),
    ],
)
def test_get_config_bool(raw: object, expected: bool) -> None:
    with patch(
        "miniagent.infrastructure.json_config.get_config",
        return_value=raw,
    ):
        assert get_config_bool("agent.parallel_sessions", True) is expected


def test_resolve_active_session_key_router_none() -> None:
    assert resolve_active_session_key(None, "fallback-id") == "fallback-id"


def test_resolve_active_session_key_unbound() -> None:
    router = ChannelRouter()
    assert resolve_active_session_key(router, "fallback-id") == ChannelRouter.CLI_CHANNEL


def test_resolve_active_session_key_bound() -> None:
    router = ChannelRouter()
    router.bind(ChannelRouter.CLI_CHANNEL, "work-a")
    assert resolve_active_session_key(router, "default") == "work-a"


def test_resolve_active_session_key_resolve_error() -> None:
    class BadRouter:
        def resolve(self, channel_id: str) -> str:
            raise RuntimeError("boom")

    assert resolve_active_session_key(BadRouter(), "safe") == "safe"
