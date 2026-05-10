"""context_overflow_strategy 与 ContextBudgetExceeded。"""

import pytest

from miniagent.memory.context import ContextBudgetExceeded, DefaultContextManager


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


def test_overflow_summarize_inserts_placeholder_when_heavy_history() -> None:
    cm = DefaultContextManager(4000, 0.2, [], overflow_strategy="summarize")
    cm.init("s", "u")
    for i in range(8):
        cm.append({"role": "assistant", "content": "reply " * 200})
        cm.append({"role": "tool", "tool_call_id": f"id{i}", "content": "tool " * 200})
    assert cm._compressed
    roles = [m.get("role") for m in cm.get_messages()]
    assert "system" in roles
