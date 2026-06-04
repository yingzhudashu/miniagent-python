"""conversation_history_for_llm：thinking 映射给 LLM 时的长度上限。"""

from __future__ import annotations

import pytest

from miniagent.memory import history_bridge as hb


def test_thinking_passed_through_when_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_CARD_THINKING_MAX_CHARS", "10000")
    hist = [{"role": "thinking", "content": "short"}]
    out = hb.conversation_history_for_llm(hist)
    assert len(out) == 1
    assert "short" in out[0]["content"]
    assert "截断" not in out[0]["content"]


def test_thinking_truncated_for_llm_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_CARD_THINKING_MAX_CHARS", "20")
    long_body = "a" * 50
    hist = [{"role": "thinking", "content": long_body}]
    raw_copy = hist[0]["content"]
    out = hb.conversation_history_for_llm(hist)
    assert hist[0]["content"] == raw_copy
    assert "history.json" in out[0]["content"]
    assert long_body not in out[0]["content"]


def test_thinking_zero_means_no_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_CARD_THINKING_MAX_CHARS", "0")
    long_body = "x" * 5000
    hist = [{"role": "thinking", "content": long_body}]
    out = hb.conversation_history_for_llm(hist)
    assert long_body in out[0]["content"]


def test_estimate_tokens_for_thinking_uses_same_cap_as_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIAGENT_FEISHU_CARD_THINKING_MAX_CHARS", "50")
    long_body = "b" * 200
    hist = [{"role": "thinking", "content": long_body}]
    t_est = hb.estimate_history_messages_tokens(hist)
    mapped = hb.conversation_history_for_llm(hist)
    from miniagent.memory.context import estimate_tokens

    t_mapped = estimate_tokens(mapped[0]["content"]) + 5
    assert t_est == t_mapped
