"""Feishu thinking-card outbound PATCH deduplication tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from miniagent.assistant.engine.thinking import _SessionThinkingState
from miniagent.assistant.feishu import thinking_delivery as poll_server
from miniagent.ui.feishu.types import FeishuConfig


@pytest.mark.asyncio
async def test_unchanged_stream_card_does_not_consume_patch_budget(monkeypatch) -> None:
    state = _SessionThinkingState()
    state.feishu_patch_budget = 5
    create = AsyncMock(return_value="message-id")
    patch = AsyncMock(return_value=True)
    monkeypatch.setattr(poll_server, "_create_interactive_thinking_message_async", create)
    monkeypatch.setattr(poll_server, "_patch_interactive_thinking_message_async", patch)
    config = FeishuConfig(app_id="app", app_secret="secret")

    await poll_server.push_feishu_thinking_stream(
        config,
        "chat",
        "same body",
        "gray",
        state,
        new_round=False,
    )
    state.feishu_last_patch_monotonic = 0.0
    budget_after_create = state.feishu_patch_budget

    await poll_server.push_feishu_thinking_stream(
        config,
        "chat",
        "same body",
        "gray",
        state,
        new_round=False,
    )

    create.assert_awaited_once()
    patch.assert_not_awaited()
    assert state.feishu_patch_budget == budget_after_create


@pytest.mark.asyncio
async def test_changed_stream_card_patches_and_records_only_on_success(monkeypatch) -> None:
    state = _SessionThinkingState()
    state.feishu_thinking_message_id = "message-id"
    state.feishu_last_patch_monotonic = 0.0
    state.feishu_last_patched_char_len = 0
    state.feishu_patch_budget = 5
    patch = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(poll_server, "_patch_interactive_thinking_message_async", patch)
    config = FeishuConfig(app_id="app", app_secret="secret")

    await poll_server.push_feishu_thinking_stream(
        config,
        "chat",
        "changed body",
        "gray",
        state,
        new_round=False,
    )
    assert state.feishu_last_sent_card_json is None
    assert state.feishu_patch_budget == 5

    await poll_server.push_feishu_thinking_stream(
        config,
        "chat",
        "changed body",
        "gray",
        state,
        new_round=False,
    )
    assert patch.await_count == 2
    assert state.feishu_last_sent_card_json is not None
    assert state.feishu_patch_budget == 4


@pytest.mark.asyncio
async def test_finalize_skips_identical_last_card_and_resets_state(monkeypatch) -> None:
    state = _SessionThinkingState()
    state.feishu_thinking_message_id = "message-id"
    state.feishu_stream_accumulated = "short body"
    config = FeishuConfig(app_id="app", app_secret="secret")
    state.feishu_last_sent_card_json = poll_server._thinking_card_json_cached(
        state, "short body", "gray", None
    )
    patch = AsyncMock(return_value=True)
    monkeypatch.setattr(poll_server, "_patch_interactive_thinking_message_async", patch)

    await poll_server.finalize_feishu_thinking_stream(config, "chat", "gray", state)

    patch.assert_not_awaited()
    assert state.feishu_thinking_message_id is None
    assert state.feishu_last_sent_card_json is None
