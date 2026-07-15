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
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _environment_metadata() -> dict[str, Any]:
    """Return reproducibility metadata without exposing endpoint credentials."""
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_sha = "unknown"
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pid": os.getpid(),
    }


def _scan_trace_for_secrets(trace_file: Path) -> dict[str, Any]:
    """Scan persisted metrics for configured credentials without returning them."""
    if not trace_file.exists():
        return {"hit_count": 0, "labels": []}
    content = trace_file.read_text(encoding="utf-8", errors="replace")
    labels: list[str] = []
    for name in (
        "OPENAI_API_KEY",
        "MINIAGENT_EMBED_API_KEY",
        "FEISHU_APP_SECRET",
        "LARK_APP_SECRET",
    ):
        secret = os.environ.get(name, "")
        if len(secret) >= 8 and secret in content:
            labels.append(name)
    if re.search(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}", content, re.IGNORECASE):
        labels.append("bearer_token_pattern")
    if re.search(r"\bsk-[A-Za-z0-9_-]{12,}", content):
        labels.append("api_key_pattern")
    return {"hit_count": len(labels), "labels": sorted(set(labels))}


def _summarize_runs(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = {}
    for result in results:
        grouped.setdefault(str(result["scenario"]), []).append(float(result["elapsed_s"]))
    summary: dict[str, Any] = {}
    for scenario, samples in grouped.items():
        ordered = sorted(samples)
        p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        summary[scenario] = {
            "count": len(ordered),
            "median_s": statistics.median(ordered),
            "p95_s": ordered[p95_index],
        }
    return summary


def _setup(perf_root: Path) -> Path:
    """Install an in-memory isolated config while preserving real model credentials."""
    from miniagent.assistant.infrastructure.json_config import (
        JsonConfigLoader,
        install_config_loader,
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = (perf_root / f"trace-{run_id}-pid{os.getpid()}").resolve()
    run_dir.mkdir(parents=True, exist_ok=False)

    base_loader = JsonConfigLoader()
    base_loader.reload(strict=True)
    # This harness deliberately overlays only non-secret runtime paths in memory.
    # It neither writes nor copies config.user.json to the result directory.
    loader = base_loader.with_runtime_overrides(
        {
            "trace": {
                "enabled": True,
                "output_dir": str(run_dir / "trace"),
                "record_payload": "metrics_only",
                "resource_sample_interval_seconds": 0.25,
                "auto_cleanup": False,
            },
            "paths": {"state_dir": str(run_dir / "state")},
            "knowledge": {
                "root": str(run_dir / "knowledge"),
                "auto_mount": False,
            },
        }
    )
    install_config_loader(loader)

    from miniagent.assistant.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()
    return run_dir


async def _one_run(
    prompt: str,
    run_idx: int,
    ctx: Any,
    toolboxes: list[Any],
    *,
    scenario: str = "custom",
    required_tools: tuple[str, ...] = (),
) -> dict[str, Any]:
    from miniagent.agent.agent import run_agent
    from miniagent.agent.observability import emit_trace

    session_key = f"perf-trace-{os.getpid()}-{scenario}-{run_idx}"
    emit_trace(
        {
            "type": "harness.run_start",
            "session_key": session_key,
            "run": run_idx,
            "scenario": scenario,
        }
    )

    t0 = time.perf_counter()
    reply = await run_agent(
        prompt,
        registry=ctx.registry,
        memory=ctx.memory,
        knowledge_registry=ctx.knowledge_registry,
        client=ctx.openai_client,
        monitor=ctx.monitor,
        toolboxes=toolboxes,
        agent_config={"max_turns": 6, "streaming": True, "debug": False},
        session_key=session_key,
        clawhub=ctx.clawhub,
        engine=ctx.engine,
    )
    elapsed = time.perf_counter() - t0
    missing_tools = sorted(set(required_tools) - set(reply.used_tools))
    if missing_tools:
        raise RuntimeError(f"scenario {scenario} did not call required tools: {missing_tools}")

    emit_trace(
        {
            "type": "harness.run_end",
            "session_key": session_key,
            "run": run_idx,
            "scenario": scenario,
            "duration_ms": int(elapsed * 1000),
            "reply_len": len(reply.reply or ""),
        }
    )
    return {
        "session_key": session_key,
        "scenario": scenario,
        "elapsed_s": elapsed,
        "reply_len": len(reply.reply or ""),
        "used_tools": list(reply.used_tools),
    }


async def _close_container_resources(ctx: Any) -> None:
    """Close every process-owned resource constructed by the composition root."""
    from miniagent.llm.openai_client_compat import close_async_openai_client

    failures: list[str] = []
    async_closers = [
        ("background_tasks", ctx.background_tasks.shutdown),
        ("message_queue", ctx.message_queue.shutdown),
        ("memory_async", ctx.memory.shutdown),
        ("openai_client", lambda: close_async_openai_client(ctx.openai_client)),
    ]
    if ctx.clawhub is not None:
        async_closers.append(("clawhub", ctx.clawhub.close))
    for name, close in async_closers:
        try:
            await close()
        except Exception:
            failures.append(name)
    ctx.openai_client = None
    try:
        ctx.memory.close()
    except Exception:
        failures.append("memory_persist")
    if failures:
        print("Resource cleanup warnings: " + ", ".join(failures))


async def _main_async(args: argparse.Namespace, run_dir: Path) -> None:
    from miniagent.agent.observability import (
        auto_register_trace_file_hook,
        get_actual_trace_file,
        shutdown_trace_writer,
    )
    from miniagent.assistant.bootstrap.entrypoint import create_application_container
    from miniagent.assistant.engine.builtin_tools import register_builtin_tools
    from miniagent.assistant.skills.builtin_toolboxes import BUILTIN_TOOLBOXES

    auto_register_trace_file_hook()
    results: list[dict[str, Any]] = []
    trace_file: Path | None = None
    writer_stats: dict[str, Any] | None = None
    ctx: Any | None = None
    try:
        ctx = create_application_container()
        register_builtin_tools(ctx.registry)
        all_toolboxes = list(BUILTIN_TOOLBOXES)
        read_toolboxes = [toolbox for toolbox in all_toolboxes if toolbox.id == "file_read"]
        read_dir_toolboxes = [
            toolbox for toolbox in all_toolboxes if toolbox.id in {"file_read", "dir_ops"}
        ]
        safe_custom_toolboxes = [
            toolbox
            for toolbox in all_toolboxes
            if toolbox.id in {"file_read", "dir_ops", "core", "knowledge"}
        ]
        scenario_specs = {
            "custom": (args.prompt, safe_custom_toolboxes, ()),
            "pure": (
                "请用三句话准确解释 Python 异步事件循环，不要调用任何工具。",
                [],
                (),
            ),
            "single": (
                "必须调用 read_file 读取 README.md 的前 5 行，然后准确概括，不要调用其他工具。",
                read_toolboxes,
                ("read_file",),
            ),
            "multi": (
                "必须先调用 list_dir 列出当前目录，再调用 read_file 读取 README.md 前 5 行，最后综合说明项目结构。",
                read_dir_toolboxes,
                ("list_dir", "read_file"),
            ),
        }
        selected = (
            ("pure", "single", "multi")
            if args.scenario == "matrix"
            else (() if args.scenario == "concurrent" else (args.scenario,))
        )
        work_items = [
            (scenario, repeat, *scenario_specs[scenario])
            for scenario in selected
            for repeat in range(args.runs)
        ]
        for i, (scenario, repeat, prompt, toolboxes, required_tools) in enumerate(work_items):
            print(f"--- {scenario} run {repeat + 1}/{args.runs} ---")
            r = await _one_run(
                prompt,
                i,
                ctx,
                toolboxes,
                scenario=scenario,
                required_tools=required_tools,
            )
            print(f"  elapsed={r['elapsed_s']:.2f}s reply_len={r['reply_len']}")
            results.append(r)
        if args.scenario in {"matrix", "concurrent"}:
            print("--- concurrent round (3 requests) ---")
            start_idx = len(results)
            concurrent_results = await asyncio.gather(
                *(
                    _one_run(
                        f"请用两句话说明 Python 异步编程的一个优势。请求编号 {index + 1}，不要调用工具。",
                        start_idx + index,
                        ctx,
                        [],
                        scenario="concurrent",
                    )
                    for index in range(3)
                )
            )
            results.extend(concurrent_results)
    finally:
        trace_file = get_actual_trace_file()
        try:
            if ctx is not None:
                await _close_container_resources(ctx)
        finally:
            writer_stats = shutdown_trace_writer()

    print("\n=== writer stats ===")
    print(json.dumps(writer_stats, ensure_ascii=False, indent=2))

    # Aggregate the trace into a phase report.
    from miniagent.assistant.infrastructure import trace_stats

    report = trace_stats.generate_daily_report()
    print("\n=== daily trace report ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if trace_file:
        print(f"\nTrace file: {trace_file}")
        breakdown = _phase_latency_breakdown(Path(str(trace_file)))
        secret_scan = _scan_trace_for_secrets(Path(str(trace_file)))
        validation_errors: list[str] = []
        if writer_stats is None:
            validation_errors.append("missing writer stats")
        else:
            for key in (
                "dropped_count",
                "serialization_error_count",
                "write_error_count",
            ):
                if int(writer_stats.get(key, 0) or 0) != 0:
                    validation_errors.append(f"writer {key} is non-zero")
            if writer_stats.get("shutdown_incomplete"):
                validation_errors.append("writer shutdown incomplete")
        if breakdown.get("unmatched_llm_requests"):
            validation_errors.append("unmatched LLM requests")
        if breakdown.get("unmatched_llm_responses"):
            validation_errors.append("unmatched LLM responses")
        event_counts = breakdown.get("event_counts", {})
        if event_counts.get("error.collect", 0):
            validation_errors.append("terminal error trace events present")
        if secret_scan["hit_count"]:
            validation_errors.append("persisted trace contains a secret pattern")
        summary_payload = {
            "environment": _environment_metadata(),
            "runs": results,
            "run_summary": _summarize_runs(results),
            "writer": writer_stats,
            "report": report,
            "breakdown": breakdown,
            "secret_scan": secret_scan,
            "validation_errors": validation_errors,
        }
        (run_dir / "summary.json").write_text(
            json.dumps(
                summary_payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if validation_errors:
            raise RuntimeError("real API trace validation failed: " + "; ".join(validation_errors))


def _phase_latency_breakdown(trace_file: Path) -> dict[str, Any]:
    """Summarize per-phase latency and request/response pairing from one trace shard."""
    if not trace_file.exists():
        print("(trace file missing)")
        return {"total_events": 0, "missing_trace": True}
    def llm_key(event: dict[str, Any]) -> tuple[Any, ...]:
        call_id = event.get("call_id")
        if call_id:
            return ("call_id", call_id)
        return (
            event.get("session_key"),
            event.get("phase"),
            event.get("turn"),
            event.get("attempt", 1),
        )

    by_type: Counter[str] = Counter()
    requests: Counter[tuple[Any, ...]] = Counter()
    responses: Counter[tuple[Any, ...]] = Counter()

    def iter_events() -> Iterator[dict[str, Any]]:
        with trace_file.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type", "?"))
                by_type[event_type] += 1
                if event_type == "llm.request":
                    requests[llm_key(event)] += 1
                elif event_type == "llm.response":
                    responses[llm_key(event)] += 1
                yield event

    from miniagent.assistant.infrastructure.trace_stats import aggregate_trace_stats

    stats = aggregate_trace_stats(iter_events())
    unmatched_requests = sum((requests - responses).values())
    unmatched_responses = sum((responses - requests).values())
    result = {
        "total_events": stats["total_events"],
        "event_counts": dict(sorted(by_type.items())),
        "llm": stats["llm"],
        "tools": stats["tools"],
        "unmatched_llm_requests": unmatched_requests,
        "unmatched_llm_responses": unmatched_responses,
    }
    print(f"\n=== phase breakdown ({stats['total_events']} events) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--prompt",
        default="用一句话解释什么是快速排序，然后读取 README.md 的前 5 行并总结。",
    )
    p.add_argument("--runs", type=int, default=1)
    p.add_argument(
        "--scenario",
        choices=("custom", "pure", "single", "multi", "concurrent", "matrix"),
        default="custom",
    )
    args = p.parse_args()

    if os.environ.get("MINIAGENT_REAL_API_STRESS") != "1":
        raise SystemExit("Set MINIAGENT_REAL_API_STRESS=1 to run the real-API trace harness.")

    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    perf_root = Path(
        os.environ.get("MINIAGENT_REAL_API_PERF_DIR", "workspaces/logs/perf")
    )
    run_dir = _setup(perf_root)
    asyncio.run(_main_async(args, run_dir))


if __name__ == "__main__":
    main()
