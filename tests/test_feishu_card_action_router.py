"""卡片按钮 value → 入站文本。"""

from __future__ import annotations

from miniagent.feishu.cards.action_router import inbound_text_from_card_action_value
from miniagent.feishu.cards.dedupe import CardActionDeduplicator


def test_inbound_text_miniagent_text() -> None:
    t = inbound_text_from_card_action_value(
        {"miniagent_text": "确认", "chat_id": "oc_x", "action_id": "ok"}
    )
    assert t == "确认"


def test_inbound_text_action_id_with_form() -> None:
    t = inbound_text_from_card_action_value(
        {"action_id": "submit", "form": {"name": "a"}, "chat_id": "oc_x"}
    )
    assert t is not None
    assert "action_id=submit" in t
    assert "payload=" in t


def test_dedupe_skips_repeat() -> None:
    deduplicator = CardActionDeduplicator()
    assert deduplicator.should_skip("k1") is False
    assert deduplicator.should_skip("k1") is True


def test_card_dedupe_is_scoped_to_runtime() -> None:
    first = CardActionDeduplicator()
    second = CardActionDeduplicator()
    assert first.should_skip("same-action") is False
    assert first.should_skip("same-action") is True
    assert second.should_skip("same-action") is False
