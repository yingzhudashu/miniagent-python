"""飞书回复分片发送策略：中途失败中止，且不重复全文 text 回退。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("lark_oapi")


def test_send_interactive_reply_cards_stops_after_failed_shard() -> None:
    from miniagent.feishu.poll_server import _send_interactive_reply_cards
    from miniagent.feishu.types import FeishuConfig

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


def test_send_plain_text_chunks_sends_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    import miniagent.feishu.poll_server as ps

    monkeypatch.setattr(ps, "feishu_card_body_max", lambda: 8)
    from miniagent.feishu.poll_server import _send_plain_text_chunks
    from miniagent.feishu.types import FeishuConfig

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

