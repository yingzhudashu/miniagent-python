"""Contract tests for the standalone Trace overhead benchmark."""

from scripts.perf_trace_overhead import run_benchmark


def test_trace_overhead_benchmark_uses_real_writer_without_drops() -> None:
    result = run_benchmark(events=100, repeats=3)

    assert result["disabled_median_ns_per_event"] > 0
    assert result["enabled_median_ns_per_event"] > 0
    assert result["writer"]["emitted_count"] == 300
    assert result["writer"]["written_count"] == 300
    assert result["writer"]["dropped_count"] == 0
