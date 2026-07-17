"""conversation_history_for_llm：thinking 映射给 LLM 时的长度上限。"""

from __future__ import annotations

from miniagent.agent import history as hb
from tests.support.config import install_test_config


def test_thinking_passed_through_when_under_cap(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"memory": {"thinking_for_llm_mode": "compact", "thinking_for_llm_compact_max_chars": 10000}},
    )
    hist = [{"role": "thinking", "content": "short"}]
    out = hb.conversation_history_for_llm(hist)
    assert len(out) == 1
    assert "思考过程摘要" in out[0]["content"]
    assert "short" in out[0]["content"]
    assert "截断" not in out[0]["content"]


def test_thinking_compact_truncated_for_llm_only(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"memory": {"thinking_for_llm_mode": "compact", "thinking_for_llm_compact_max_chars": 20}},
    )
    long_body = "a" * 50
    hist = [{"role": "thinking", "content": long_body}]
    raw_copy = hist[0]["content"]
    out = hb.conversation_history_for_llm(hist)
    assert hist[0]["content"] == raw_copy
    assert "history.json" in out[0]["content"]
    assert long_body not in out[0]["content"]


def test_thinking_full_zero_means_no_truncation(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"memory": {"thinking_for_llm_mode": "full", "thinking_for_llm_max_chars": 0}},
    )
    long_body = "x" * 5000
    hist = [{"role": "thinking", "content": long_body}]
    out = hb.conversation_history_for_llm(hist)
    assert long_body in out[0]["content"]


def test_thinking_off_skips_thinking(tmp_path) -> None:
    install_test_config(tmp_path, {"memory": {"thinking_for_llm_mode": "off"}})
    hist = [
        {"role": "thinking", "content": "hidden"},
        {"role": "assistant", "content": "visible"},
    ]
    out = hb.conversation_history_for_llm(hist)
    assert out == [{"role": "assistant", "content": "visible"}]


def test_thinking_full_uses_full_cap(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {
            "memory": {
                "thinking_for_llm_mode": "full",
                "thinking_for_llm_max_chars": 20,
                "thinking_for_llm_compact_max_chars": 10000,
            }
        },
    )
    long_body = "z" * 50
    out = hb.conversation_history_for_llm([{"role": "thinking", "content": long_body}])
    assert "思考过程）" in out[0]["content"]
    assert "history.json" in out[0]["content"]
    assert long_body not in out[0]["content"]


def test_estimate_tokens_for_thinking_uses_same_cap_as_llm(tmp_path) -> None:
    install_test_config(
        tmp_path,
        {"memory": {"thinking_for_llm_mode": "compact", "thinking_for_llm_compact_max_chars": 50}},
    )
    long_body = "b" * 200
    hist = [{"role": "thinking", "content": long_body}]
    t_est = hb.estimate_history_messages_tokens(hist)
    mapped = hb.conversation_history_for_llm(hist)
    from miniagent.agent.context import estimate_tokens

    t_mapped = estimate_tokens(mapped[0]["content"]) + 5
    assert t_est == t_mapped


def test_format_history_budget_estimates_each_message_once(monkeypatch) -> None:
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 40}
        for index in range(200)
    ]
    calls = 0
    real_estimate = hb._message_token_estimate

    def counting_estimate(message):
        nonlocal calls
        calls += 1
        return real_estimate(message)

    monkeypatch.setattr(hb, "_message_token_estimate", counting_estimate)
    result = hb.format_history_for_llm(history, max_tokens=100)

    assert result
    assert calls == len(history)
