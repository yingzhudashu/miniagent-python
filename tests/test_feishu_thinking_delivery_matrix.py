"""Feishu thinking delivery state-machine edge contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from miniagent.engine.thinking import _SessionThinkingState
from miniagent.feishu import thinking_delivery as delivery
from miniagent.feishu.types import FeishuConfig


@pytest.fixture
def config() -> FeishuConfig:
    return FeishuConfig(app_id="app", app_secret="secret")


def test_create_message_sync_success_and_failure(monkeypatch, config) -> None:
    post = MagicMock(side_effect=[(True, "mid"), (False, None), (True, None)])
    monkeypatch.setattr(delivery, "_post_interactive_message", post)

    assert delivery._create_interactive_thinking_message(config, "chat", "{}") == "mid"
    assert delivery._create_interactive_thinking_message(config, "chat", "{}") is None
    assert delivery._create_interactive_thinking_message(config, "chat", "{}") is None


@pytest.mark.asyncio
async def test_create_and_patch_message_async_contracts(monkeypatch, config) -> None:
    post = AsyncMock(side_effect=[(True, "mid"), (False, None)])
    monkeypatch.setattr(delivery, "_post_interactive_message_async", post)
    assert await delivery._create_interactive_thinking_message_async(
        config, "chat", "{}", reply_to_message_id="reply", reply_in_thread=True
    ) == "mid"
    assert await delivery._create_interactive_thinking_message_async(
        config, "chat", "{}"
    ) is None

    patch = AsyncMock(side_effect=[(False, "denied"), (True, None)])
    monkeypatch.setattr("miniagent.feishu.im_send.patch_im_message_async", patch)
    assert not await delivery._patch_interactive_thinking_message_async(
        config, "mid", "{}"
    )
    assert await delivery._patch_interactive_thinking_message_async(
        config, "mid", "{}"
    )


@pytest.mark.asyncio
async def test_push_invalid_chat_create_failure_and_pending_content(
    monkeypatch, config
) -> None:
    create = AsyncMock(return_value=None)
    monkeypatch.setattr(delivery, "_create_interactive_thinking_message_async", create)
    state = _SessionThinkingState()
    await delivery.push_feishu_thinking_stream(
        config, "", "ignored", "gray", state, new_round=False
    )
    create.assert_not_awaited()

    state.feishu_pending_header = "execute"
    state.feishu_pending_tool_lines = ["\n- pending"]
    state.feishu_reply_to_message_id = "reply"
    state.feishu_reply_in_thread = True
    await delivery.push_feishu_thinking_stream(
        config, "chat", "body", "gray", state, new_round=False
    )
    assert "execute" in state.feishu_stream_accumulated
    assert "pending" in state.feishu_stream_accumulated
    assert state.feishu_pending_tool_lines == []
    assert state.feishu_tool_section_started is True
    assert state.feishu_thinking_message_id is None
    assert create.await_args.kwargs["reply_to_message_id"] == "reply"


@pytest.mark.asyncio
async def test_push_new_round_preserves_tool_section_and_patch_budget(
    monkeypatch, config
) -> None:
    marker = "\n\n**\u5de5\u5177**"
    state = _SessionThinkingState()
    state.feishu_thinking_message_id = "mid"
    state.feishu_stream_accumulated = "old reasoning" + marker + "\n\n- tool"
    state.feishu_tool_section_started = True
    state.feishu_last_patch_monotonic = 0.0
    state.feishu_last_patched_char_len = 0
    state.feishu_patch_budget = 1
    patch = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery, "_patch_interactive_thinking_message_async", patch)
    monkeypatch.setattr(delivery, "_is_important_content_for_immediate_patch", lambda _text: True)

    await delivery.push_feishu_thinking_stream(
        config, "chat", "new reasoning", "gray", state, new_round=True
    )
    assert "old reasoning" in state.feishu_stream_accumulated
    assert "new reasoning" in state.feishu_stream_accumulated
    assert "tool" in state.feishu_stream_accumulated
    assert "---" in state.feishu_stream_accumulated
    patch.assert_awaited_once()
    assert state.feishu_patch_budget >= 0

    state.feishu_patch_budget = 0
    patch.reset_mock()
    await delivery.push_feishu_thinking_stream(
        config, "chat", "different", "gray", state, new_round=False
    )
    patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_guards_patch_failure_and_continuations(monkeypatch, config) -> None:
    state = _SessionThinkingState()
    await delivery.finalize_feishu_thinking_stream(config, "chat", "gray", state)
    assert state.feishu_thinking_message_id is None

    state.feishu_thinking_message_id = "mid"
    state.feishu_stream_accumulated = "   "
    await delivery.finalize_feishu_thinking_stream(config, "chat", "gray", state)
    assert state.feishu_thinking_message_id is None

    state.feishu_thinking_message_id = "mid"
    state.feishu_stream_accumulated = "body"
    monkeypatch.setattr(delivery, "_chunk_feishu_card_markdown", lambda *_args, **_kwargs: [])
    await delivery.finalize_feishu_thinking_stream(config, "chat", "gray", state)
    assert state.feishu_thinking_message_id == "mid"

    monkeypatch.setattr(
        delivery,
        "_chunk_feishu_card_markdown",
        lambda *_args, **_kwargs: ["first", "second", "third"],
    )
    patch = AsyncMock(return_value=False)
    post = AsyncMock(side_effect=[(True, "next"), (False, None)])
    monkeypatch.setattr(delivery, "_patch_interactive_thinking_message_async", patch)
    monkeypatch.setattr(delivery, "_post_interactive_message_async", post)
    await delivery.finalize_feishu_thinking_stream(config, "chat", "gray", state)
    assert patch.await_count == 1
    assert post.await_count == 2
    assert state.feishu_thinking_message_id == "mid"

    patch.return_value = True
    post.side_effect = [(True, "next"), (True, "last")]
    await delivery.finalize_feishu_thinking_stream(config, "chat", "gray", state)
    assert state.feishu_thinking_message_id is None


@pytest.mark.asyncio
async def test_append_tool_guards_pending_and_patch_failure(monkeypatch, config) -> None:
    state = _SessionThinkingState()
    await delivery.append_feishu_thinking_same_card(config, "", "tool", "gray", state)
    await delivery.append_feishu_thinking_same_card(config, "chat", "", "gray", state)

    state.feishu_pending_tool_lines = None
    await delivery.append_feishu_thinking_same_card(
        config, "chat", "first\nline", "gray", state
    )
    assert len(state.feishu_pending_tool_lines) == 1
    await delivery.append_feishu_thinking_same_card(
        config, "chat", "second", "gray", state
    )
    assert len(state.feishu_pending_tool_lines) == 2

    state.feishu_thinking_message_id = "mid"
    patch = AsyncMock(return_value=False)
    monkeypatch.setattr(delivery, "_patch_interactive_thinking_message_async", patch)
    await delivery.append_feishu_thinking_same_card(
        config, "chat", "third", "gray", state
    )
    patch.assert_awaited_once()

    state.feishu_last_sent_card_json = delivery._thinking_card_json_cached(
        state, state.feishu_stream_accumulated, "gray", None
    )
    patch.reset_mock()
    await delivery.append_feishu_thinking_same_card(
        config, "chat", "", "gray", state
    )
    patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_thinking_invalid_failure_and_exception(monkeypatch, config) -> None:
    post = AsyncMock(side_effect=[(False, None), RuntimeError("offline")])
    monkeypatch.setattr(delivery, "_post_interactive_message_async", post)

    await delivery._send_thinking(config, "", "ignored")
    await delivery._send_thinking(
        config,
        "chat",
        "thinking",
        reply_to_message_id="reply",
        reply_in_thread=True,
    )
    await delivery._send_thinking(config, "chat", "thinking again")

    assert post.await_count == 2


def test_thinking_card_cache_tracks_confirmation_state(monkeypatch) -> None:
    state = _SessionThinkingState()
    channel = SimpleNamespace(has_pending=True)
    engine = SimpleNamespace(get_confirmation_channel=lambda _key: channel)
    first = delivery._thinking_card_json_cached(state, "body", "gray", "s", engine)
    second = delivery._thinking_card_json_cached(state, "body", "gray", "s", engine)
    assert first == second
    channel.has_pending = False
    assert delivery._thinking_card_json_cached(state, "body", "gray", "s", engine) != first

