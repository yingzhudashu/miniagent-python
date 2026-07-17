"""飞书 JSON 配置默认值行为。"""

from __future__ import annotations

from tests.support.config import install_test_config


def test_feishu_reply_plain_default_off(tmp_path) -> None:
    from miniagent.assistant.feishu.poll_server import _feishu_reply_plain_enabled

    install_test_config(tmp_path)
    assert _feishu_reply_plain_enabled() is False
    install_test_config(tmp_path, {"feishu": {"reply_plain": True}})
    assert _feishu_reply_plain_enabled() is True


def test_feishu_reply_plain_explicit_false(tmp_path) -> None:
    from miniagent.assistant.feishu.poll_server import _feishu_reply_plain_enabled

    install_test_config(tmp_path, {"feishu": {"reply_plain": False}})
    assert _feishu_reply_plain_enabled() is False
