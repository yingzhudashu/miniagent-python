#!/usr/bin/env python3
"""Run a bounded local mixed-workload stability soak with full Trace validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from miniagent.agent.context import DefaultContextManager
from miniagent.agent.observability import (
    TraceResourceSampler,
    TraceRuntimeConfig,
    auto_register_trace_file_hook,
    clear_trace_hooks,
    emit_trace,
    get_actual_trace_file,
    get_trace_writer_stats,
    shutdown_trace_writer,
    trace_span,
)
from miniagent.agent.types.memory import MemoryEntryInput
from miniagent.assistant.infrastructure.trace_stats import aggregate_trace_stats
from miniagent.assistant.memory.keyword_index import KeywordIndex
from miniagent.assistant.memory.store import DefaultMemoryStore

_WARMUP_ITERATIONS = 200


def _iter_events(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event


def _wait_for_trace_queue_empty(timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while (get_trace_writer_stats() or {}).get("queue_depth", 0):
        if time.monotonic() >= deadline:
            raise TimeoutError("Trace writer did not drain its warmup queue")
        time.sleep(0.01)


async def run_soak(
    *,
    duration_seconds: float = 1_800,
    interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Exercise bounded context, Trace, cache, and durable-memory paths."""
    duration_seconds = max(0.05, float(duration_seconds))
    interval_seconds = max(0.001, float(interval_seconds))
    clear_trace_hooks()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        keyword_index = KeywordIndex(state_dir=str(root / "state"))
        store = DefaultMemoryStore(
            state_dir=str(root / "state"),
            keyword_index=keyword_index,
            embedding_provider=None,
        )
        context = DefaultContextManager(
            context_window=32_000,
            compress_threshold=0.9,
            tools=[],
            overflow_strategy="truncate",
        )
        context.init("stable system prompt", "initial message")
        sampler = TraceResourceSampler(
            min(0.1, interval_seconds),
            track_python_allocations=True,
        )
        auto_register_trace_file_hook(
            TraceRuntimeConfig(
                enabled=True,
                output_dir=str(root / "trace"),
                writer_batch_interval=0.01,
                writer_batch_size=250,
                writer_queue_max_size=20_000,
            )
        )

        async def exercise(iteration: int) -> str:
            session = f"soak-{iteration % 25}"
            context.append(
                {"role": "user", "content": f"bounded user message {iteration % 100}"}
            )
            context.append(
                {
                    "role": "assistant",
                    "content": f"bounded assistant message {iteration % 100}",
                }
            )
            if len(context.get_messages()) > 200:
                context.compress()
            context.get_token_report()
            if iteration % 20 == 0:
                slot = iteration % 100
                entry = MemoryEntryInput(
                    timestamp=f"2026-01-01T00:00:{slot:02d}+00:00",
                    user_snippet=f"soak input {slot}",
                    summary=f"soak summary {slot}",
                    facts=[f"iteration slot {slot}"],
                )
                await store.record_turn(session, entry.summary, entry.facts, entry)
            return session

        for warmup in range(_WARMUP_ITERATIONS):
            session = f"soak-{warmup % 25}"
            with trace_span("soak.warmup", session_key=session):
                await exercise(warmup)
            emit_trace({"type": "perf.soak_warmup", "run": warmup})
        await store.flush_keyword_index_async()
        await asyncio.to_thread(_wait_for_trace_queue_empty)
        baseline_threads = threading.active_count()
        sampler.start()
        started = time.monotonic()
        iterations = 0
        try:
            while time.monotonic() - started < duration_seconds:
                workload_iteration = _WARMUP_ITERATIONS + iterations
                session = f"soak-{workload_iteration % 25}"
                with trace_span("soak.iteration", session_key=session):
                    await exercise(workload_iteration)
                emit_trace(
                    {
                        "type": "perf.soak_iteration",
                        "session_key": session,
                        "run": iterations,
                        "message_count": len(context.get_messages()),
                    }
                )
                iterations += 1
                await asyncio.sleep(interval_seconds)

            await store.flush_keyword_index_async()
        finally:
            sampler.shutdown()
            trace_file = get_actual_trace_file()
            writer = shutdown_trace_writer()
        assert trace_file is not None
        report = aggregate_trace_stats(_iter_events(trace_file))

    final_threads = threading.active_count()
    clear_trace_hooks()
    errors: list[str] = []
    if writer is None:
        errors.append("missing writer stats")
    else:
        for key in ("dropped_count", "serialization_error_count", "write_error_count"):
            if int(writer.get(key, 0) or 0):
                errors.append(f"writer {key} is non-zero")
        if writer.get("shutdown_incomplete"):
            errors.append("writer shutdown incomplete")
    resources = report.get("resources", {})
    growth = resources.get("rss_warm_to_final_growth_ratio")
    if isinstance(growth, int | float) and growth > 0.05:
        errors.append(f"RSS warm-to-final growth exceeds 5%: {growth:.4f}")
    python_growth = resources.get("python_warm_to_final_growth_ratio")
    if isinstance(python_growth, int | float) and python_growth > 0.05:
        errors.append(f"Python warm-to-final growth exceeds 5%: {python_growth:.4f}")
    if final_threads > baseline_threads + 1:
        errors.append(
            f"thread count did not return to baseline: {baseline_threads} -> {final_threads}"
        )
    return {
        "schema_version": 1,
        "duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "iterations": iterations,
        "baseline_threads": baseline_threads,
        "final_threads": final_threads,
        "writer": writer,
        "resources": resources,
        "spans": report.get("spans", {}),
        "validation_errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=1_800)
    parser.add_argument("--interval-seconds", type=float, default=0.1)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    payload = asyncio.run(
        run_soak(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
        )
    )
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if args.json_out:
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if payload["validation_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
