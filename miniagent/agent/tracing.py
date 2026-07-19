"""Per-AgentRuntime JSONL event exporter with metrics-only persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from miniagent.agent.events import AgentEvent
from miniagent.agent.lifecycle import HealthReport, HealthState

if TYPE_CHECKING:
    from miniagent.agent.runtime import AgentRuntime

_SENSITIVE_PAYLOAD_KEYS = {
    "arguments",
    "content",
    "plan",
    "reflection",
    "reply",
    "result",
    "text",
}


class JsonlTraceExporter:
    """Lifecycle extension exporting one runtime's events without global hooks."""

    extension_id = "trace"
    name = "trace"

    def __init__(
        self,
        output_path: str | Path,
        *,
        queue_size: int = 10_000,
        record_payload: str = "metrics_only",
    ) -> None:
        if queue_size < 1:
            raise ValueError("trace queue_size must be at least 1")
        if record_payload not in {"metrics_only", "full"}:
            raise ValueError("record_payload must be 'metrics_only' or 'full'")
        self.output_path = Path(output_path)
        self.record_payload = record_payload
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(queue_size)
        self._runtime: AgentRuntime | None = None
        self._unsubscribe: Any = None
        self._writer: asyncio.Task[None] | None = None
        self._state = HealthState.STOPPED
        self._written = 0
        self._dropped = 0

    def bind(self, runtime: AgentRuntime) -> None:
        """Subscribe to exactly one Agent runtime before lifecycle startup."""
        if self._runtime is not None and self._runtime is not runtime:
            raise RuntimeError("trace exporter is already bound to another AgentRuntime")
        self._runtime = runtime
        if self._unsubscribe is None:
            self._unsubscribe = runtime.subscribe(self._on_event)

    async def initialize(self) -> None:
        """Create the output directory without starting background work."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = HealthState.STARTING

    async def start(self) -> None:
        """Start the single queue writer after the runtime has been bound."""
        if self._runtime is None:
            raise RuntimeError("trace exporter must be bound before start")
        self._writer = asyncio.create_task(self._write_loop(), name="agent-trace-writer")
        self._state = HealthState.READY

    async def stop(self) -> None:
        """Flush queued events, stop the writer and unsubscribe idempotently."""
        if self._writer is not None:
            await self._queue.put(None)
            await self._writer
            self._writer = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._state = HealthState.STOPPED

    def health(self) -> HealthReport:
        """Report writer readiness, overflow degradation and counters."""
        state = self._state
        if state is HealthState.READY and self._dropped:
            state = HealthState.DEGRADED
        return HealthReport(
            state,
            "trace queue overflow" if self._dropped else "",
            {"written": self._written, "dropped": self._dropped},
        )

    def _on_event(self, event: AgentEvent) -> None:
        payload = self._serialize(event)
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._dropped += 1

    def _serialize(self, event: AgentEvent) -> dict[str, Any]:
        payload = dict(event.payload)
        if self.record_payload == "metrics_only":
            payload = {
                key: value
                for key, value in payload.items()
                if key not in _SENSITIVE_PAYLOAD_KEYS
                and isinstance(value, str | int | float | bool | type(None))
            }
        else:
            payload = {key: self._json_safe(value) for key, value in payload.items()}
        return {
            "event_id": event.event_id,
            "kind": event.kind.value,
            "run_id": event.run_id,
            "session_id": event.session_id,
            "trace_id": event.trace_id,
            "sequence": event.sequence,
            "occurred_at": event.occurred_at.isoformat(),
            "payload": payload,
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, dict):
            return {str(key): JsonlTraceExporter._json_safe(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [JsonlTraceExporter._json_safe(item) for item in value]
        return repr(value)

    async def _write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            line = json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
            await asyncio.to_thread(self._append, line)
            self._written += 1

    def _append(self, line: str) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(line)


__all__ = ["JsonlTraceExporter"]
