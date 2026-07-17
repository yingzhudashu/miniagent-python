"""Map managed background work into platform-neutral inbound contracts."""

from __future__ import annotations

from miniagent.ui.messages import InboundMessage

BACKGROUND_CHANNEL = "background"
BACKGROUND_SENDER_ID = "background-task-manager"


def build_background_inbound_message(
    task_id: str,
    session_key: str,
    prompt: str,
    *,
    parent_session_key: str | None = None,
) -> InboundMessage:
    """Build one isolated background turn while preserving an empty prompt."""
    return InboundMessage.create(
        event_id=task_id,
        channel=BACKGROUND_CHANNEL,
        conversation_id=session_key,
        sender_id=BACKGROUND_SENDER_ID,
        content=prompt if prompt else "\n",
        session_key=session_key,
        idempotency_key=task_id,
        trace_id=task_id,
        metadata={
            "task_id": task_id,
            "parent_session_key": parent_session_key,
            "empty_prompt": not bool(prompt),
        },
    )


def background_prompt(message: InboundMessage) -> str:
    """Recover the exact task prompt, including the empty-string case."""
    if message.metadata.get("empty_prompt") is True:
        return ""
    return message.content


__all__ = [
    "BACKGROUND_CHANNEL",
    "BACKGROUND_SENDER_ID",
    "background_prompt",
    "build_background_inbound_message",
]
