"""Focused regressions migrated from test_final_diff_coverage_matrix.py."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miniagent.assistant.feishu import card_rendering

schedule_tools = importlib.import_module("miniagent.assistant.tools.schedule_tools")

def test_card_rendering_empty_cap_and_fence_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not card_rendering.is_important_content_for_immediate_patch("")
    assert card_rendering.normalize_lark_md("") == ""
    assert card_rendering.prepare_thinking_body_for_card("abcdef", max_len=3) == "abc…"
    assert card_rendering._chunk_cut_index("```python\ncode", 5) == len("```python\ncode")
    monkeypatch.setattr(card_rendering, "FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE", True)
    assert card_rendering.is_important_content_for_immediate_patch("# title")
    assert card_rendering.is_important_content_for_immediate_patch("|a|b|")

def test_feishu_auto_bind_and_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.agent.types.confirmation import ConfirmationStage
    from miniagent.assistant.engine.feishu_handler import _FeishuHandlerRuntime

    runtime = object.__new__(_FeishuHandlerRuntime)
    router = SimpleNamespace(
        FEISHU_P2P_PREFIX="feishu_p2p:",
        is_bound=lambda _channel: False,
        bind=MagicMock(),
    )
    runtime.channel_router = router
    runtime.state = {"active_session_id": "active", "feishu_p2p_synced_senders": []}
    monkeypatch.setattr(
        "miniagent.assistant.infrastructure.cli_feishu_policy.should_allow_p2p_auto_bind", lambda _router: True
    )
    runtime.maybe_auto_bind("p2p", "sender")
    router.bind.assert_called_once()
    assert runtime.state["feishu_p2p_synced_senders"] == {"sender"}

    channel = SimpleNamespace(
        has_pending=True,
        pending=SimpleNamespace(stage=ConfirmationStage.CLARIFICATION),
        respond=MagicMock(),
    )
    assert runtime._respond_clarification(channel, "answer")
    channel.respond.assert_called_once()
