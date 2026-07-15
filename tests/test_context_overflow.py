"""context_overflow_strategy 与 ContextBudgetExceeded。"""

import pytest

from miniagent.agent.context import ContextBudgetExceeded, DefaultContextManager
from miniagent.agent.observability import register_trace_hook, unregister_trace_hook


def test_overflow_error_raises_on_append() -> None:
    cm = DefaultContextManager(1500, 0.25, [], overflow_strategy="error")
    cm.init("system", "user" * 800)
    with pytest.raises(ContextBudgetExceeded):
        cm.append({"role": "assistant", "content": "assistant" * 1200})


def test_overflow_truncate_drops_middle_messages() -> None:
    cm = DefaultContextManager(8000, 0.99, [], overflow_strategy="truncate")
    cm.init("s", "u0")
    cm.append({"role": "assistant", "content": "a1"})
    cm.append({"role": "user", "content": "u2"})
    cm.append({"role": "assistant", "content": "a3"})
    assert len(cm.get_messages()) >= 2
    cm._compress_truncate()
    assert cm._compressed


def test_overflow_truncate_emits_compatible_trace_metrics() -> None:
    events = []
    register_trace_hook(events.append)
    try:
        cm = DefaultContextManager(
            100,
            0.2,
            [],
            overflow_strategy="truncate",
            session_key="trace-session",
        )
        cm.init("s", "u")
        cm._messages.extend(
            {"role": "assistant", "content": "x" * 100}
            for _ in range(20)
        )
        cm._recalculate_tokens()
        cm._compress_truncate()
    finally:
        unregister_trace_hook(events.append)

    event = events[-1]
    assert event["type"] == "context.compress"
    assert event["strategy"] == "truncate"
    assert event["session_key"] == "trace-session"
    assert event["before_tokens"] > event["after_tokens"]
    assert event["removed_count"] > 0


def test_overflow_summarize_inserts_placeholder_when_heavy_history() -> None:
    cm = DefaultContextManager(4000, 0.2, [], overflow_strategy="summarize")
    cm.init("s", "u")
    for i in range(8):
        cm.append({"role": "assistant", "content": "reply " * 200})
        cm.append({"role": "tool", "tool_call_id": f"id{i}", "content": "tool " * 200})
    assert cm._compressed
    roles = [m.get("role") for m in cm.get_messages()]
    assert "system" in roles


def test_append_redacts_multiple_tool_messages_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIAGENT_MEMORY_CONTEXT_TOOL_REDACT", "1")
    cm = DefaultContextManager(18_000, 0.06, [], overflow_strategy="summarize")
    cm.init("s", "u")
    cm.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ],
        }
    )
    cm.append({"role": "tool", "tool_call_id": "1", "content": "A" * 7000})
    cm.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "2", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ],
        }
    )
    cm.append({"role": "tool", "tool_call_id": "2", "content": "B" * 7000})
    tools = [m for m in cm.get_messages() if m.get("role") == "tool"]
    assert len(tools) == 2
    assert "A" * 10 not in (tools[0].get("content") or "")
    assert "B" * 10 not in (tools[1].get("content") or "")


def test_tool_redact_runs_before_summarize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_MEMORY_CONTEXT_TOOL_REDACT", "1")
    cm = DefaultContextManager(12_000, 0.15, [], overflow_strategy="summarize")
    cm.init("s", "u")
    cm.append(
        {
            "role": "assistant",
            "content": "a",
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
            ],
        }
    )
    cm.append({"role": "tool", "tool_call_id": "1", "content": "HUGE " * 4000})
    msgs = cm.get_messages()
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert tool_msgs
    assert "HUGE" not in (tool_msgs[0].get("content") or "")
