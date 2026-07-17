"""Test-only request payload workload tests."""

from __future__ import annotations

import pytest

from tests.support.request_payload import serialize_exec_payload_sample


def _minimal_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "tool_0",
                "description": "x" * 50,
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_serialize_returns_positive_lengths() -> None:
    ml, tl = serialize_exec_payload_sample(_minimal_tools(), user_turn_pairs=6)
    assert ml > 1000
    assert tl > 50


def test_serialize_user_turn_pairs_zero() -> None:
    ml, tl = serialize_exec_payload_sample(_minimal_tools(), user_turn_pairs=0)
    assert ml > 0
    assert tl > 0
    # init only: system + user
    assert ml < serialize_exec_payload_sample(_minimal_tools(), user_turn_pairs=1)[0]


def test_serialize_rejects_negative_user_turn_pairs() -> None:
    with pytest.raises(ValueError, match="user_turn_pairs must be >= 0"):
        serialize_exec_payload_sample(_minimal_tools(), user_turn_pairs=-1)
