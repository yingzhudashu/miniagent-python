"""Tests for the opt-in real API trace harness without making network calls."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.perf_trace_real_api import _phase_latency_breakdown


def test_phase_latency_breakdown_reports_pairing_and_responses_tokens(
    tmp_path: Path,
) -> None:
    trace_file = tmp_path / "trace.jsonl"
    events = [
        {
            "type": "llm.request",
            "session_key": "safe-session",
            "phase": "plan",
            "attempt": 1,
        },
        {
            "type": "llm.response",
            "session_key": "safe-session",
            "phase": "plan",
            "attempt": 1,
            "duration_ms": 250,
            "usage": {"input_tokens": 30, "output_tokens": 12},
        },
        {
            "type": "llm.request",
            "session_key": "safe-session",
            "phase": "exec",
            "turn": 1,
            "attempt": 1,
        },
    ]
    trace_file.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    result = _phase_latency_breakdown(trace_file)

    assert result["total_events"] == 3
    assert result["unmatched_llm_requests"] == 1
    assert result["unmatched_llm_responses"] == 0
    assert result["llm"]["total_tokens"]["prompt"] == 30
    assert result["llm"]["by_phase"]["plan"]["p95_duration_ms"] == 250


def test_phase_latency_breakdown_handles_missing_file(tmp_path: Path) -> None:
    result = _phase_latency_breakdown(tmp_path / "missing.jsonl")

    assert result == {"total_events": 0, "missing_trace": True}
