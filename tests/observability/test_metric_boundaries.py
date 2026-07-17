"""Focused regressions migrated from test_type_boundary_regressions.py."""

from __future__ import annotations

from miniagent.assistant.infrastructure.trace_stats import _TraceStatsAccumulator


def test_trace_memory_chars_accepts_numeric_metrics() -> None:
    stats = _TraceStatsAccumulator()
    stats._add_memory_read({"duration_ms": 1.5, "chars_loaded": 12.0})
    assert stats.memory_total_chars == 12
