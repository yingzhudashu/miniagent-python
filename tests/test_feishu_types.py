"""Tests for feishu types."""

from miniagent.feishu.types import FeishuConfig, FeishuMessageEvent, FeishuReply


class TestFeishuConfig:
    def test_default_port(self):
        cfg = FeishuConfig(app_id="test", app_secret="secret")
        assert cfg.port == 0

    def test_custom_port(self):
        cfg = FeishuConfig(app_id="test", app_secret="secret", port=8080)
        assert cfg.port == 8080

    def test_optional_fields(self):
        cfg = FeishuConfig(app_id="test", app_secret="secret")
        assert cfg.encrypt_key is None
        assert cfg.verification_token is None


class TestFeishuMessageEvent:
    def test_create(self):
        event = FeishuMessageEvent(
            message_id="msg_001",
            chat_id="chat_001",
            sender_id="sender_001",
            msg_type="text",
            content="Hello",
        )
        assert event.message_id == "msg_001"
        assert event.content == "Hello"


class TestFeishuReply:
    def test_defaults(self):
        reply = FeishuReply(content="Hi")
        assert reply.msg_type == "text"
        assert reply.receive_id_type == "chat_id"

    def test_custom_type(self):
        reply = FeishuReply(content="image", msg_type="image")
        assert reply.msg_type == "image"
