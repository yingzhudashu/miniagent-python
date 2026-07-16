"""Direct failure and fallback contracts for Feishu outbound delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.assistant.feishu import outbound_delivery as delivery
from miniagent.assistant.feishu.types import FeishuConfig


@pytest.fixture
def config() -> FeishuConfig:
    return FeishuConfig("app", "secret")


def test_post_message_helpers_map_failures_and_missing_ids(monkeypatch, config) -> None:
    post = MagicMock(side_effect=[(False, None, "bad"), (True, None, None), (True, "mid", None)])
    monkeypatch.setattr("miniagent.assistant.feishu.im_send.post_im_message", post)
    assert delivery._post_interactive_message(
        config, receive_id="chat", card_json="{}"
    ) == (False, None)
    assert delivery._post_interactive_message(
        config, receive_id="chat", card_json="{}"
    ) == (False, None)
    assert delivery._post_interactive_message(
        config, receive_id="chat", card_json="{}"
    ) == (True, "mid")

    post.side_effect = [(False, None, "bad"), (True, "mid", None)]
    assert not delivery._post_text_message(
        config, receive_id="chat", text_content_json='{"text":"x"}'
    )
    assert delivery._post_text_message(
        config, receive_id="chat", text_content_json='{"text":"x"}'
    )


@pytest.mark.asyncio
async def test_async_post_message_helper_matrix(monkeypatch, config) -> None:
    post = AsyncMock(
        side_effect=[
            (False, None, "bad"),
            (True, None, None),
            (True, "mid", None),
        ]
    )
    monkeypatch.setattr("miniagent.assistant.feishu.im_send.post_im_message_async", post)
    assert await delivery._post_interactive_message_async(
        config, receive_id="chat", card_json="{}"
    ) == (False, None)
    assert await delivery._post_interactive_message_async(
        config, receive_id="chat", card_json="{}"
    ) == (False, None)
    assert await delivery._post_interactive_message_async(
        config, receive_id="chat", card_json="{}"
    ) == (True, "mid")


def test_interactive_cards_and_plain_chunks_matrix(monkeypatch, config) -> None:
    assert delivery._send_interactive_reply_cards(config, "chat", []) == (0, 0)
    post = MagicMock(side_effect=[(True, "one"), (False, None)])
    monkeypatch.setattr(delivery, "_post_interactive_message", post)
    assert delivery._send_interactive_reply_cards(
        config, "chat", ["one", "two", "three"]
    ) == (1, 3)

    monkeypatch.setattr(delivery._card_rendering, "chunk_card_markdown", lambda _text: [])
    text_post = MagicMock()
    monkeypatch.setattr(delivery, "_post_text_message", text_post)
    delivery._send_plain_text_chunks(config, "chat", "")
    text_post.assert_not_called()

    monkeypatch.setattr(
        delivery._card_rendering, "chunk_card_markdown", lambda _text: ["a", "b"]
    )
    text_post.side_effect = [True, False]
    delivery._send_plain_text_chunks(config, "chat", "body", reason="fallback")
    assert text_post.call_count == 2
    monkeypatch.setattr(
        delivery._card_rendering,
        "chunk_card_markdown",
        MagicMock(side_effect=RuntimeError("bad")),
    )
    delivery._send_plain_text_chunks(config, "chat", "body")


@pytest.mark.asyncio
async def test_send_reply_invalid_success_partial_and_full_fallback(monkeypatch, config) -> None:
    fallback = MagicMock()
    monkeypatch.setattr(delivery, "_send_plain_text_chunks", fallback)
    monkeypatch.setattr(
        delivery._card_rendering, "is_valid_im_receive_id", lambda value: value != "bad"
    )
    await delivery._send_reply(config, "bad", "ignored")
    fallback.assert_not_called()

    monkeypatch.setattr(
        delivery._card_rendering, "chunk_card_markdown", lambda _text: ["a", "b"]
    )
    monkeypatch.setattr(delivery, "_feishu_reply_plain_enabled", lambda: True)
    monkeypatch.setattr(
        delivery._card_rendering,
        "strip_light_markdown_for_plain",
        lambda value: value.strip("*"),
    )
    send = MagicMock(return_value=(2, 2))
    monkeypatch.setattr(delivery, "_send_interactive_reply_cards", send)
    await delivery._send_reply(config, "chat", "**body**")
    fallback.assert_not_called()

    send.return_value = (1, 2)
    await delivery._send_reply(config, "chat", "body")
    assert fallback.call_args.kwargs["reason"] == "partial_card_send_notice"

    send.return_value = (0, 2)
    await delivery._send_reply(config, "chat", "body")
    assert fallback.call_args.kwargs["reason"] == "interactive_reply_failed_full_fallback"

    send.side_effect = ImportError("missing")
    await delivery._send_reply(config, "chat", "body")
    assert fallback.call_args.kwargs["reason"] == "lark_oapi_import_error"

    send.side_effect = RuntimeError("failed")
    await delivery._send_reply(config, "chat", "body")
    assert fallback.call_args.kwargs["reason"] == "interactive_reply_failed_full_fallback"
