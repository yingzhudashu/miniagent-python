"""Real-API end-to-end trace harness.

Runs the full agent pipeline (classify -> plan -> execute -> reflect) against the
configured real LLM endpoint with trace persistence enabled, then aggregates the
resulting trace JSONL into a per-phase latency report.

Purpose: surface where wall-clock time actually goes on a real run, and exercise
the trace system end-to-end so its bugs show up in practice.

Usage (bash):
    export MINIAGENT_REAL_API_STRESS=1
    PYTHONUTF8=1 python scripts/perf_trace_real_api.py --prompt "..." --runs 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


def _setup() -> None:
    from miniagent.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()


async def _one_run(prompt: str, run_idx: int) -> dict[str, Any]:
    from miniagent.core.agent import run_agent
    from miniagent.engine.builtin_tools import register_builtin_tools
    from miniagent.infrastructure.registry import DefaultToolRegistry
    from miniagent.infrastructure.tracing import emit_trace, get_trace_writer_stats
    from miniagent.skills.builtin_toolboxes import BUILTIN_TOOLBOXES

    registry = DefaultToolRegistry()
    register_builtin_tools(registry)
    toolboxes = list(BUILTIN_TOOLBOXES)

    session_key = f"perf-trace-{int(time.time())}-{run_idx}"
    emit_trace({"type": "harness.run_start", "session_key": session_key, "run": run_idx})

    t0 = time.perf_counter()
    reply = await run_agent(
        prompt,
        registry=registry,
        toolboxes=toolboxes,
        agent_config={"max_turns": 6, "streaming": True, "debug": False},
        session_key=session_key,
    )
    elapsed = time.perf_counter() - t0

    emit_trace(
        {
            "type": "harness.run_end",
            "session_key": session_key,
            "run": run_idx,
            "duration_ms": int(elapsed * 1000),
            "reply_len": len(reply or ""),
        }
    )
    return {
        "session_key": session_key,
        "elapsed_s": elapsed,
        "reply_len": len(reply or ""),
        "writer_stats": get_trace_writer_stats(),
    }


async def _main_async(args: argparse.Namespace) -> None:
    # Enable trace persistence before importing anything that reads config.
    from miniagent.infrastructure.json_config import JsonConfigLoader
    from miniagent.infrastructure.tracing import (
        auto_register_trace_file_hook,
        get_actual_trace_file,
        shutdown_trace_writer,
    )

    loader = JsonConfigLoader.get_instance()
    loader._load()
    loader._user.setdefault("trace", {})
    loader._user["trace"]["enabled"] = True
    loader._user["trace"]["record_payload"] = "metrics_only"
    auto_register_trace_file_hook()

    results = []
    try:
        for i in range(args.runs):
            print(f"--- run {i + 1}/{args.runs} ---")
            r = await _one_run(args.prompt, i)
            print(f"  elapsed={r['elapsed_s']:.2f}s reply_len={r['reply_len']}")
            results.append(r)
    finally:
        trace_file = get_actual_trace_file()
        shutdown_trace_writer()

    print("\n=== writer stats (last run) ===")
    if results:
        print(json.dumps(results[-1]["writer_stats"], ensure_ascii=False, indent=2))

    # Aggregate the trace into a phase report.
    from miniagent.infrastructure import trace_stats

    report = trace_stats.generate_daily_report()
    print("\n=== daily trace report ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if trace_file:
        print(f"\nTrace file: {trace_file}")
        _phase_latency_breakdown(Path(str(trace_file)))


def _phase_latency_breakdown(trace_file: Path) -> None:
    """Pair llm.request/response and tool.start/end per session to show phase timing."""
    if not trace_file.exists():
        print("(trace file missing)")
        return
    events = []
    with trace_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    print(f"\n=== phase breakdown ({len(events)} events) ===")
    by_type: dict[str, int] = {}
    for e in events:
        by_type[e.get("type", "?")] = by_type.get(e.get("type", "?"), 0) + 1
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {t:30s} {c}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--prompt",
        default="用一句话解释什么是快速排序，然后读取 README.md 的前 5 行并总结。",
    )
    p.add_argument("--runs", type=int, default=1)
    args = p.parse_args()

    if os.environ.get("MINIAGENT_REAL_API_STRESS") != "1":
        raise SystemExit("Set MINIAGENT_REAL_API_STRESS=1 to run the real-API trace harness.")

    _setup()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
