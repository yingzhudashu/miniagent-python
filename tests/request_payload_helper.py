"""Test-only serialization workload; deliberately excluded from the wheel."""

from __future__ import annotations

import json
from typing import Any

from miniagent.agent.context import DefaultContextManager


def serialize_exec_payload_sample(
    tools: list[dict[str, Any]], *, user_turn_pairs: int = 6
) -> tuple[int, int]:
    if user_turn_pairs < 0:
        raise ValueError(f"user_turn_pairs must be >= 0, got {user_turn_pairs}")
    context = DefaultContextManager(
        context_window=128_000,
        compress_threshold=0.99,
        tools=tools,  # type: ignore[arg-type]
        overflow_strategy="summarize",
    )
    context.init("system " * 400, "user " * 400)
    for _ in range(user_turn_pairs):
        context.append({"role": "assistant", "content": "a" * 400})
        context.append({"role": "user", "content": "b" * 200})
    messages_json = json.dumps(context.get_messages(), ensure_ascii=False)
    tools_json = json.dumps(tools, ensure_ascii=False)
    return len(messages_json), len(tools_json)


__all__ = ["serialize_exec_payload_sample"]
