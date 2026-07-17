"""Focused regressions migrated from test_recovery_edge_matrix.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from miniagent.llm.providers import openai_transport as llm_transport


def test_responses_fallback_and_stream_event_edges() -> None:
    empty = llm_transport._response_fallback_events("")
    text = llm_transport._response_fallback_events("answer")
    assert len(empty) == 1 and empty[0].completed
    assert text[0].content_delta == "answer" and text[-1].completed

    response = {
        "output_text": "done",
        "output": [
            {"type": "function_call", "call_id": "call", "name": "tool", "arguments": "{}"}
        ],
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }
    events = llm_transport._response_fallback_events(response)
    assert any(event.tool_call_delta for event in events)
    assert events[-1].incomplete_reason == "max_output_tokens"

    state = llm_transport._ResponseEventState()
    assert llm_transport._normalize_response_stream_event(
        SimpleNamespace(type="response.output_item.done", item=SimpleNamespace(type="message")),
        state,
    ) == []
    assert llm_transport._normalize_response_stream_event(
        SimpleNamespace(type="unknown"), state
    ) == []
    with pytest.raises(llm_transport.LLMTransportError):
        llm_transport._normalize_response_stream_event(
            SimpleNamespace(type="response.failed"), state
        )
