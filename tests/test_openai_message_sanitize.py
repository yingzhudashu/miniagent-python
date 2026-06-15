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
    assert out[0] == {"role": "user", "content": "hi"}
    assert out[1] == {"role": "assistant", "content": "ok"}


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
    assert out[0]["tool_calls"] == [
        {
            "id": "1",
            "type": "function",
            "function": {"name": "f", "arguments": "{}"},
        }
    ]


def test_does_not_mutate_input() -> None:
    msgs = [
        {"role": "user", "content": "hi", "_internal": 1},
    ]
    original = msgs[0].copy()
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert msgs[0] == original
    assert out[0] is not msgs[0]


def test_empty_list() -> None:
    assert strip_leading_underscore_keys_from_messages([]) == []


def test_skips_non_dict_items() -> None:
    msgs: list = [
        "not a message",
        None,
        {"role": "user", "content": "ok", "_trace": True},
    ]
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert len(out) == 1
    assert out[0] == {"role": "user", "content": "ok"}


def test_does_not_strip_nested_function_underscore_keys() -> None:
    """仅两层清洗：function 等更深层嵌套内的 ``_*`` 键不在本模块职责内。"""
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}", "_internal": True},
                }
            ],
        }
    ]
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert out[0]["tool_calls"][0]["function"]["_internal"] is True


def test_tool_calls_non_dict_items_preserved() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": ["opaque", {"id": "1", "_meta": 0, "type": "function"}],
        }
    ]
    out = strip_leading_underscore_keys_from_messages(msgs)
    assert out[0]["tool_calls"][0] == "opaque"
    assert out[0]["tool_calls"][1] == {"id": "1", "type": "function"}
