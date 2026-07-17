"""Tests for feishu types."""

from miniagent.ui.feishu.types import FeishuConfig


class TestFeishuConfig:
    def test_optional_fields(self):
        cfg = FeishuConfig(app_id="test", app_secret="secret")
        assert cfg.encrypt_key is None
        assert cfg.verification_token is None
