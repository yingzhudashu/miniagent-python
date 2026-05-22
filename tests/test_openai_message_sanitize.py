"""openai_message_sanitize：API 前剥离 ``_*`` 键。"""

from __future__ import annotations

from miniagent.core.openai_message_sanitize import strip_leading_underscore_keys_from_messages


def test_strip_top_level_underscore_keys() -> None:
    msgs = [
        {"role": "user", "content": "hi", "_internal": 1},
        {"role": "assistant", "content": "ok", "_x": {"nested": True}},
    ]
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert len(out) == 2
    assert "_internal" not in out[0]
    assert out[0]["content"] == "hi"


def test_strip_tool_calls_child_keys() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                    "_meta": 9,
                }
            ],
        }
    ]
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert "_meta" not in out[0]["tool_calls"][0]
