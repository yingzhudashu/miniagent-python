"""飞书回复路由与 ``feishu.reply_target`` 配置相关单测。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.config_helpers import install_test_config

pytest.importorskip("lark_oapi")


def test_feishu_outbound_reply_params_default_reply(tmp_path) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(tmp_path)
    assert feishu_outbound_reply_params("om_123") == ("om_123", False)


def test_feishu_outbound_reply_params_explicit_create(tmp_path) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(tmp_path, {"feishu": {"reply_target": "create"}})
    assert feishu_outbound_reply_params("om_123") == (None, False)


def test_feishu_outbound_reply_params_reply_mode(tmp_path) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(
        tmp_path,
        {"feishu": {"reply_target": "reply", "reply_in_thread": True}},
    )
    assert feishu_outbound_reply_params("om_abc") == ("om_abc", True)


def test_feishu_outbound_reply_params_invalid_target_falls_back(tmp_path) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(tmp_path, {"feishu": {"reply_target": "typo"}})
    assert feishu_outbound_reply_params("om_123") == (None, False)


def test_feishu_outbound_reply_params_thread_id_default_in_thread(tmp_path) -> None:
    """未设置 ``feishu.reply_in_thread`` 时，``thread_id`` 非空则默认话题内回复。"""
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(tmp_path, {"feishu": {"reply_target": "reply"}})
    assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", True)


def test_feishu_outbound_reply_params_explicit_off_overrides_thread(tmp_path) -> None:
    from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

    install_test_config(
        tmp_path,
        {"feishu": {"reply_target": "reply", "reply_in_thread": False}},
    )
    assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", False)


def test_send_interactive_reply_cards_uses_reply_api_when_configured() -> None:
    from miniagent.assistant.feishu.lark_client import clear_client_cache
    from miniagent.assistant.feishu.poll_server import _send_interactive_reply_cards
    from miniagent.assistant.feishu.types import FeishuConfig

    # Clear client cache to ensure mock is used
    clear_client_cache()

    cfg = FeishuConfig(app_id="a", app_secret="b", verification_token="t")
    client = MagicMock()
    ok = MagicMock()
    ok.success.return_value = True
    ok.data = MagicMock()
    ok.data.message_id = "new_mid"
    client.im.v1.message.reply.return_value = ok

    mock_builder = MagicMock()
    mock_builder.app_id.return_value = mock_builder
    mock_builder.app_secret.return_value = mock_builder
    mock_builder.build.return_value = client

    with patch("lark_oapi.Client.builder", return_value=mock_builder):
        sent, total = _send_interactive_reply_cards(
            cfg,
            "oc_test",
            ["only"],
            reply_to_message_id="om_parent",
            reply_in_thread=True,
        )

    assert sent == 1 and total == 1
    client.im.v1.message.reply.assert_called_once()
    client.im.v1.message.create.assert_not_called()
