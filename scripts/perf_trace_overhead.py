#!/usr/bin/env python3
"""Measure production ``emit_trace`` overhead with persistence off and on."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from miniagent.agent.observability import (
    TraceRuntimeConfig,
    auto_register_trace_file_hook,
    clear_trace_hooks,
    emit_trace,
    get_trace_writer_stats,
    shutdown_trace_writer,
    trace_span,
)


def _sample_ns_per_event(
    events: int,
    repeats: int,
    emit: Callable[[int], None],
    *,
    after_repeat: Callable[[], None] | None = None,
) -> list[float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for index in range(events):
            emit(index)
        samples.append((time.perf_counter_ns() - started) / events)
        if after_repeat is not None:
            after_repeat()
    return samples


def _wait_for_writer_drain(timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while int((get_trace_writer_stats() or {}).get("queue_depth", 0) or 0):
        if time.monotonic() >= deadline:
            raise TimeoutError("Trace writer did not drain during overhead benchmark")
        time.sleep(0.001)


def run_benchmark(*, events: int = 2_000, repeats: int = 7) -> dict[str, Any]:
    """Return comparable fast-path and metrics-only persistence samples."""
    events = max(1, int(events))
    repeats = max(1, int(repeats))
    clear_trace_hooks()
    for index in range(200):
        emit_trace({"type": "perf.trace_warmup", "run": index})
    disabled = _sample_ns_per_event(
        events,
        repeats,
        lambda index: emit_trace({"type": "perf.trace_disabled", "run": index}),
    )
    disabled_spans = _sample_ns_per_event(
        events,
        repeats,
        lambda _index: _run_span(),
    )

    with tempfile.TemporaryDirectory() as tmp:
        auto_register_trace_file_hook(
            TraceRuntimeConfig(
                enabled=True,
                output_dir=tmp,
                writer_batch_interval=0.01,
                writer_batch_size=1_000,
                writer_queue_max_size=events * 2 + 1_000,
                record_payload="metrics_only",
            )
        )
        enabled = _sample_ns_per_event(
            events,
            repeats,
            lambda index: emit_trace(
                {
                    "type": "perf.trace_enabled",
                    "run": index,
                    "duration_ms": 1.25,
                    "content": "must-not-persist",
                }
            ),
            after_repeat=_wait_for_writer_drain,
        )
        enabled_spans = _sample_ns_per_event(
            events,
            repeats,
            lambda _index: _run_span(),
            after_repeat=_wait_for_writer_drain,
        )
        stats = shutdown_trace_writer()

    clear_trace_hooks()
    return {
        "schema_version": 2,
        "events_per_repeat": events,
        "repeats": repeats,
        "disabled_median_ns_per_event": statistics.median(disabled),
        "enabled_median_ns_per_event": statistics.median(enabled),
        "disabled_span_median_ns": statistics.median(disabled_spans),
        "enabled_span_median_ns": statistics.median(enabled_spans),
        "disabled_samples_ns_per_event": disabled,
        "enabled_samples_ns_per_event": enabled,
        "disabled_span_samples_ns": disabled_spans,
        "enabled_span_samples_ns": enabled_spans,
        "writer": stats,
    }


def _run_span() -> None:
    with trace_span("perf.trace_span"):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=2_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    payload = run_benchmark(events=args.events, repeats=args.repeats)
    print(json.dumps(payload, indent=2))
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    writer = payload.get("writer") or {}
    if any(
        int(writer.get(key, 0) or 0)
        for key in ("dropped_count", "serialization_error_count", "write_error_count")
    ):
        return 1
    if payload["enabled_median_ns_per_event"] > 50_000:
        return 1
    if payload["enabled_span_median_ns"] > 50_000:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
