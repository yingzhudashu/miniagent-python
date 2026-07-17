"""Background task standard inbound mapping tests."""

from __future__ import annotations

from miniagent.assistant.engine.background_inbound import (
    BACKGROUND_CHANNEL,
    background_prompt,
    build_background_inbound_message,
)


def test_background_message_retains_task_and_parent_identity() -> None:
    """A managed task becomes an isolated, traceable inbound turn."""
    message = build_background_inbound_message(
        "task1234",
        "__bg__task1234",
        "analyze",
        parent_session_key="default",
    )

    assert message.event_id == "task1234"
    assert message.channel == BACKGROUND_CHANNEL
    assert message.session_key == "__bg__task1234"
    assert message.content == "analyze"
    assert message.idempotency_key == "task1234"
    assert message.trace_id == "task1234"
    assert message.metadata["parent_session_key"] == "default"
    assert background_prompt(message) == "analyze"


def test_empty_prompt_round_trips_without_violating_message_contract() -> None:
    """Empty task prompts remain empty when passed to the engine."""
    message = build_background_inbound_message("empty123", "__bg__empty123", "")

    assert message.content
    assert message.metadata["empty_prompt"] is True
    assert background_prompt(message) == ""
