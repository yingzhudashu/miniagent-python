"""Tests for Feishu Reply - Merged from multiple test files.

Covers:
- Reply chunking (card markdown splitting)
- Reply routing (MINIAGENT_FEISHU_REPLY_TARGET)
- Send reply policy (failure handling, multiple chunks)

Original files merged:
- test_feishu_reply_chunking.py
- test_feishu_reply_routing.py
- test_feishu_send_reply_policy.py
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.config_helpers import install_test_config

pytest.importorskip("lark_oapi")


@pytest.fixture(autouse=True)
def _clear_lark_client_cache_for_reply_tests():
    from miniagent.assistant.feishu import im_send

    im_send.clear_client_cache()


# ============================================================================
# Reply Chunking Tests
# ============================================================================


class TestFeishuReplyChunking:
    """飞书回复按卡片正文上限分片。"""

    def test_chunk_concat_roundtrip(self) -> None:
        from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

        s = "a" * 35
        parts = chunk_card_markdown(s, max_len=12)
        assert "".join(parts) == s
        assert all(len(p) <= 12 for p in parts)

    def test_chunk_multiline_produces_multiple_segments(self) -> None:
        from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

        s = "para1\n\npara2\n\npara3\nextra-long-tail-xxxxx"
        parts = chunk_card_markdown(s, max_len=18)
        assert len(parts) >= 2
        assert all(len(p) <= 18 for p in parts)

    def test_single_chunk_when_under_cap(self) -> None:
        from miniagent.assistant.feishu.card_rendering import chunk_card_markdown

        assert chunk_card_markdown("hello", max_len=1000) == ["hello"]


# ============================================================================
# Reply Routing Tests
# ============================================================================


class TestFeishuReplyRouting:
    """飞书回复路由与 ``feishu.reply_target`` 配置相关。"""

    def test_feishu_outbound_reply_params_default_reply(self, tmp_path) -> None:
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(tmp_path)
        assert feishu_outbound_reply_params("om_123") == ("om_123", False)

    def test_feishu_outbound_reply_params_explicit_create(self, tmp_path) -> None:
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(tmp_path, {"feishu": {"reply_target": "create"}})
        assert feishu_outbound_reply_params("om_123") == (None, False)

    def test_feishu_outbound_reply_params_reply_mode(self, tmp_path) -> None:
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(
            tmp_path,
            {"feishu": {"reply_target": "reply", "reply_in_thread": True}},
        )
        assert feishu_outbound_reply_params("om_abc") == ("om_abc", True)

    def test_feishu_outbound_reply_params_invalid_target_falls_back(self, tmp_path) -> None:
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(tmp_path, {"feishu": {"reply_target": "typo"}})
        assert feishu_outbound_reply_params("om_123") == (None, False)

    def test_feishu_outbound_reply_params_thread_id_default_in_thread(self, tmp_path) -> None:
        """未设置 ``feishu.reply_in_thread`` 时，thread_id 非空则默认话题内回复。"""
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(tmp_path, {"feishu": {"reply_target": "reply"}})
        assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", True)

    def test_feishu_outbound_reply_params_explicit_off_overrides_thread(self, tmp_path) -> None:
        from miniagent.assistant.feishu.outbound_delivery import feishu_outbound_reply_params

        install_test_config(
            tmp_path,
            {"feishu": {"reply_target": "reply", "reply_in_thread": False}},
        )
        assert feishu_outbound_reply_params("om_x", "t_thread_1") == ("om_x", False)

    def test_send_interactive_reply_cards_uses_reply_api_when_configured(self) -> None:
        from miniagent.assistant.feishu.outbound_delivery import _send_interactive_reply_cards
        from miniagent.assistant.feishu.types import FeishuConfig

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


# ============================================================================
# Send Reply Policy Tests
# ============================================================================


class TestFeishuSendReplyPolicy:
    """飞书回复分片发送策略。"""

    def test_send_interactive_reply_cards_stops_after_failed_shard(self) -> None:
        from miniagent.assistant.feishu.outbound_delivery import _send_interactive_reply_cards
        from miniagent.assistant.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="a", app_secret="b", verification_token="t")
        client = MagicMock()
        ok = MagicMock()
        ok.success.return_value = True
        bad = MagicMock()
        bad.success.return_value = False
        client.im.v1.message.create.side_effect = [ok, bad, ok]

        mock_builder = MagicMock()
        mock_builder.app_id.return_value = mock_builder
        mock_builder.app_secret.return_value = mock_builder
        mock_builder.build.return_value = client

        with patch("lark_oapi.Client.builder", return_value=mock_builder):
            sent, total = _send_interactive_reply_cards(cfg, "oc_test", ["part1", "part2", "part3"])

        assert total == 3
        assert sent == 1
        assert client.im.v1.message.create.call_count == 2

    def test_send_plain_text_chunks_sends_multiple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import miniagent.assistant.feishu.outbound_delivery as ps

        monkeypatch.setattr(ps._card_rendering, "feishu_card_body_max", lambda: 8)
        from miniagent.assistant.feishu.outbound_delivery import _send_plain_text_chunks
        from miniagent.assistant.feishu.types import FeishuConfig

        cfg = FeishuConfig(app_id="a", app_secret="b", verification_token="t")
        client = MagicMock()
        ok = MagicMock()
        ok.success.return_value = True
        client.im.v1.message.create.return_value = ok

        mock_builder = MagicMock()
        mock_builder.app_id.return_value = mock_builder
        mock_builder.app_secret.return_value = mock_builder
        mock_builder.build.return_value = client

        with patch("lark_oapi.Client.builder", return_value=mock_builder):
            _send_plain_text_chunks(cfg, "oc_test", "abcdefghijklmnop")

        assert client.im.v1.message.create.call_count >= 2


__all__ = [
    "TestFeishuReplyChunking",
    "TestFeishuReplyRouting",
    "TestFeishuSendReplyPolicy",
]
