"""Tests for Feishu reply routing configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.config_helpers import install_test_config

pytest.importorskip("lark_oapi")


@pytest.mark.parametrize(
    ("overrides", "message_id", "thread_id", "expected"),
    [
        (None, "om_123", None, ("om_123", False)),
        ({"feishu": {"reply_target": "create"}}, "om_123", None, (None, False)),
        (
            {"feishu": {"reply_target": "reply", "reply_in_thread": True}},
            "om_abc",
            None,
            ("om_abc", True),
        ),
        ({"feishu": {"reply_target": "typo"}}, "om_123", None, (None, False)),
        (
            {"feishu": {"reply_target": "reply"}},
            "om_x",
            "t_thread_1",
            ("om_x", True),
        ),
        (
            {"feishu": {"reply_target": "reply", "reply_in_thread": False}},
            "om_x",
            "t_thread_1",
            ("om_x", False),
        ),
    ],
    ids=[
        "default-reply",
        "explicit-create",
        "explicit-thread-reply",
        "invalid-target",
        "thread-default",
        "thread-explicit-off",
    ],
)
def test_feishu_outbound_reply_params(
    tmp_path,
    overrides: dict | None,
    message_id: str,
    thread_id: str | None,
    expected: tuple[str | None, bool],
) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(tmp_path, overrides)
    assert feishu_outbound_reply_params(message_id, thread_id) == expected


def test_send_interactive_reply_cards_uses_reply_api_when_configured() -> None:
    from miniagent.assistant.feishu.lark_client import clear_client_cache
    from miniagent.assistant.feishu.poll_server import _send_interactive_reply_cards
    from miniagent.assistant.feishu.types import FeishuConfig

    clear_client_cache()
    config = FeishuConfig(app_id="a", app_secret="b", verification_token="t")
    client = MagicMock()
    response = MagicMock()
    response.success.return_value = True
    response.data = MagicMock()
    response.data.message_id = "new_mid"
    client.im.v1.message.reply.return_value = response
    builder = MagicMock()
    builder.app_id.return_value = builder
    builder.app_secret.return_value = builder
    builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=builder):
        sent, total = _send_interactive_reply_cards(
            config,
            "oc_test",
            ["only"],
            reply_to_message_id="om_parent",
            reply_in_thread=True,
        )

    assert sent == 1
    assert total == 1
    client.im.v1.message.reply.assert_called_once()
    client.im.v1.message.create.assert_not_called()
