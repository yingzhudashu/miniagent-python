"""飞书回复路由与环境变量 ``MINIAGENT_FEISHU_REPLY_TARGET`` 相关单测。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("lark_oapi")


def test_feishu_outbound_reply_params_default_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_TARGET", raising=False)
    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_IN_THREAD", raising=False)
    assert feishu_outbound_reply_params("om_123") == ("om_123", False)


def test_feishu_outbound_reply_params_explicit_create(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_TARGET", "create")
    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_IN_THREAD", raising=False)
    assert feishu_outbound_reply_params("om_123") == (None, False)


def test_feishu_outbound_reply_params_reply_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_TARGET", "reply")
    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_IN_THREAD", "1")
    assert feishu_outbound_reply_params("om_abc") == ("om_abc", True)


def test_feishu_outbound_reply_params_invalid_target_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_TARGET", "typo")
    assert feishu_outbound_reply_params("om_123") == (None, False)


def test_feishu_outbound_reply_params_thread_id_default_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置 ``MINIAGENT_FEISHU_REPLY_IN_THREAD`` 时，``thread_id`` 非空则默认话题内回复。"""
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_TARGET", "reply")
    monkeypatch.delenv("MINIAGENT_FEISHU_REPLY_IN_THREAD", raising=False)
    assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", True)


def test_feishu_outbound_reply_params_explicit_off_overrides_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.feishu.poll_server import feishu_outbound_reply_params

    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_TARGET", "reply")
    monkeypatch.setenv("MINIAGENT_FEISHU_REPLY_IN_THREAD", "0")
    assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", False)


def test_send_interactive_reply_cards_uses_reply_api_when_configured() -> None:
    from miniagent.feishu.poll_server import _send_interactive_reply_cards
    from miniagent.feishu.types import FeishuConfig

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
