"""轻量级 trace 钩子：供执行器发出结构化事件，可接入日志或外部 APM。

``emit_trace(event)`` 中的 ``event`` 建议为可 JSON 序列化的 ``dict``（至少含
``"kind"`` 或 ``"phase"`` 等区分字段）；具体键由调用方约定，钩子应容错未知字段。

进程内全局钩子列表；测试或子进程隔离场景可 ``clear_trace_hooks()``。

**可选持久化**：在 JSON 配置中设置 ``trace.enabled: true`` 与 ``trace.output_dir``，自动注册钩子将事件写入 JSONL 文件（``workspaces/logs/trace-YYYY-MM-DD-pid{pid}.jsonl``）。

**事件类型规范**：见 ``miniagent.agent.trace_events`` 模块。

**统计分析**：见 ``miniagent.assistant.infrastructure.trace_stats`` 模块。
"""

from __future__ import annotations

import json
import os
import queue
import re
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

TraceHook = Callable[[dict[str, Any]], None]
ConfigGetter = Callable[[str, Any], Any]


@dataclass(frozen=True, slots=True)
class TraceRuntimeConfig:
    """Process-scoped trace configuration supplied by the composition root."""

    enabled: bool = False
    debug_log_path: str = ""
    output_dir: str = "workspaces/logs"
    writer_batch_interval: float = 0.1
    writer_batch_size: int = 50
    writer_queue_max_size: int = 10_000
    writer_overflow_policy: str = "drop_oldest"
    writer_shutdown_timeout_seconds: float = 5.0
    record_payload: str = "metrics_only"
    resource_sample_interval_seconds: float = 0.0
    track_python_allocations: bool = False

    @classmethod
    def from_getter(cls, get_config: ConfigGetter) -> TraceRuntimeConfig:
        """Build a validated-by-owner snapshot without importing product config."""
        return cls(
            enabled=bool(get_config("trace.enabled", False)),
            debug_log_path=str(get_config("debug.log_path", "") or "").strip(),
            output_dir=str(get_config("trace.output_dir", "workspaces/logs")),
            writer_batch_interval=float(get_config("trace.writer_batch_interval", 0.1)),
            writer_batch_size=int(get_config("trace.writer_batch_size", 50)),
            writer_queue_max_size=int(get_config("trace.writer_queue_max_size", 10_000)),
            writer_overflow_policy=str(
                get_config("trace.writer_overflow_policy", TRACE_OVERFLOW_DROP_OLDEST)
            ),
            writer_shutdown_timeout_seconds=float(
                get_config("trace.writer_shutdown_timeout_seconds", 5.0)
            ),
            record_payload=str(
                get_config("trace.record_payload", TRACE_RECORD_PAYLOAD_METRICS_ONLY)
            ),
            resource_sample_interval_seconds=float(
                get_config("trace.resource_sample_interval_seconds", 0) or 0
            ),
            track_python_allocations=bool(
                get_config("trace.track_python_allocations", False)
            ),
        )


class _ExcludeSessionCommand:
    """FIFO maintenance command processed by the sole writer thread."""

    __slots__ = ("done", "reject_future", "removed", "session_key")

    def __init__(self, session_key: str, *, reject_future: bool = True) -> None:
        self.session_key = session_key
        self.reject_future = reject_future
        self.done = threading.Event()
        self.removed = 0


_hooks: list[TraceHook] = []

# 可选持久化配置
_TRACE_LOG_FILE: Path | None = None
_TRACE_RECORD_PAYLOAD = "metrics_only"
_TRACE_SHUTDOWN_TIMEOUT_SECONDS = 5.0

# 异步写入器实例
_trace_writer: AsyncTraceWriter | None = None
_resource_sampler: TraceResourceSampler | None = None

# 是否已自动初始化
_auto_initialized = False

# Logger
from miniagent.agent.logging import get_logger
from miniagent.agent.trace_events import TRACE_SCHEMA_VERSION

_logger = get_logger(__name__)

TRACE_OVERFLOW_DROP_NEWEST = "drop_newest"
TRACE_OVERFLOW_DROP_OLDEST = "drop_oldest"
TRACE_RECORD_PAYLOAD_METRICS_ONLY = "metrics_only"

_PERSISTENCE_DROP_KEYS = {
    "api_key",
    "args",
    "args_truncated",
    "arguments",
    "authorization",
    "body",
    "content",
    "full_content",
    "messages",
    "prompt",
    "raw",
    "request",
    "response",
    "result",
    "text",
    "thinking",
    "token",
}
_PERSISTENCE_PREVIEW_KEYS = {
    "description_preview",
    "error_message",
    "error_preview",
    "location",
}
_PERSISTENCE_SAFE_STRING_KEYS = {
    "action",
    "call_id",
    "failure_category",
    "finish_reason",
    "incomplete_reason",
    "error_type",
    "kb_name",
    "layer",
    "model",
    "parent_span_id",
    "phase",
    "proposal_id",
    "purpose",
    "reason",
    "reasoning_level",
    "render_mode",
    "risk_level",
    "scenario",
    "session_key",
    "source",
    "span_id",
    "status",
    "strategy",
    "tool",
    "tool_call_id",
    "tool_name",
    "ts",
    "type",
    "wire_api",
}
_PERSISTENCE_SAFE_STRING_LIST_KEYS = {"output_item_types", "retry_adjustments"}
_PERSISTENCE_SAFE_SCALAR_KEYS = {
    "after_tokens",
    "ambiguity_count",
    "asked_count",
    "attempt",
    "before_tokens",
    "cache_age_seconds",
    "cache_hit",
    "cache_size",
    "changed",
    "compress_ratio",
    "cpu_ms",
    "cpu_system_ms",
    "cpu_user_ms",
    "default_resolved_count",
    "duration_ms",
    "entries_count",
    "fallback_count",
    "has_tool_calls",
    "idle_seconds",
    "input_bytes",
    "input_chars",
    "index_duration_ms",
    "is_user_error",
    "json_object",
    "knowledge_resolved_count",
    "max_tokens",
    "memory_resolved_count",
    "message_chars",
    "message_count",
    "network_ms",
    "output_chars",
    "process_cpu_ms",
    "python_traced_bytes",
    "python_traced_peak_bytes",
    "protocol_fallback",
    "queue_depth",
    "queue_wait_ms",
    "removed_count",
    "removed_tokens",
    "reply_len",
    "retrying",
    "rss_bytes",
    "run",
    "sampling_removed",
    "sampler_elapsed_ms",
    "size",
    "size_measurement_truncated",
    "skipped",
    "status_code",
    "structured_stream",
    "success",
    "text_length",
    "thread_count",
    "trace_queue_depth",
    "tool_count",
    "tool_schema_chars",
    "turn",
    "unresolved_count",
    "validation_error",
    "warning_count",
    "written_blocks",
    "trace_schema_version",
}
_TRACE_DATE_RE = re.compile(r"trace-(\d{4}-\d{2}-\d{2})")
_CURRENT_TRACE_SPAN: ContextVar[str | None] = ContextVar(
    "miniagent_current_trace_span",
    default=None,
)
_CURRENT_TRACE_SESSION: ContextVar[str | None] = ContextVar(
    "miniagent_current_trace_session",
    default=None,
)
_USAGE_SCALAR_KEYS = {
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "total_tokens",
}
_USAGE_DETAIL_KEYS = {
    "completion_tokens_details",
    "input_tokens_details",
    "output_tokens_details",
    "prompt_tokens_details",
}


def _json_shape_char_count(value: Any, *, max_nodes: int = 100_000) -> tuple[int, bool]:
    """Count JSON-like key/value characters without materializing or retaining payload text."""
    total = 0
    visited = 0
    stack = [value]
    while stack:
        current = stack.pop()
        visited += 1
        if visited > max_nodes:
            return total, True
        if isinstance(current, str | bytes):
            total += len(current)
        elif isinstance(current, dict):
            for key, item in current.items():
                total += len(str(key))
                stack.append(item)
        elif isinstance(current, list | tuple):
            stack.extend(current)
        elif current is None:
            total += 4
        elif isinstance(current, bool):
            total += 4 if current else 5
        elif isinstance(current, int | float):
            total += len(str(current))
    return total, False


def llm_request_size_metrics(
    messages: Any,
    tools: Any | None = None,
    *,
    force: bool = False,
) -> dict[str, int | bool]:
    """Return payload-size scalars safe for metrics-only LLM request traces."""
    if not force and not _hooks and _trace_writer is None:
        return {}
    message_chars, message_truncated = _json_shape_char_count(messages)
    tool_chars, tool_truncated = _json_shape_char_count(tools or [])
    return {
        "message_chars": message_chars,
        "tool_schema_chars": tool_chars,
        "size_measurement_truncated": message_truncated or tool_truncated,
    }


def _sanitize_usage_metrics(usage: dict[Any, Any]) -> dict[str, Any]:
    """Keep only numeric token counters from an SDK usage payload."""
    sanitized: dict[str, Any] = {}
    for key, value in usage.items():
        if not isinstance(key, str):
            continue
        if (
            key in _USAGE_SCALAR_KEYS
            and isinstance(value, int | float)
            and not isinstance(value, bool)
        ):
            sanitized[key] = value
        elif key in _USAGE_DETAIL_KEYS and isinstance(value, dict):
            details = {
                detail_key: detail_value
                for detail_key, detail_value in value.items()
                if isinstance(detail_key, str)
                and isinstance(detail_value, int | float)
                and not isinstance(detail_value, bool)
            }
            if details:
                sanitized[key] = details
    return sanitized


def new_trace_id(prefix: str = "trace") -> str:
    """Return a compact process-local correlation identifier."""
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "", prefix)[:16] or "trace"
    return f"{normalized}-{uuid4().hex[:16]}"


@contextmanager
def trace_parent(span_id: str, *, session_key: str | None = None):
    """Bind the parent span inherited by nested async work."""
    token = _CURRENT_TRACE_SPAN.set(span_id)
    session_token = _CURRENT_TRACE_SESSION.set(session_key) if session_key is not None else None
    try:
        yield
    finally:
        if session_token is not None:
            _CURRENT_TRACE_SESSION.reset(session_token)
        _CURRENT_TRACE_SPAN.reset(token)


@contextmanager
def trace_span(
    phase: str,
    *,
    session_key: str | None = None,
    parent_span_id: str | None = None,
    span_id: str | None = None,
):
    """Emit a low-overhead start/end span around synchronous or async work.

    The context manager itself is synchronous on purpose: it can wrap code that
    contains ``await`` without introducing another task or altering cancellation
    semantics.
    """
    actual_span_id = span_id or new_trace_id("span")
    actual_parent_span_id = parent_span_id or _CURRENT_TRACE_SPAN.get()
    started_wall_ns = time.monotonic_ns()
    started_cpu_ns = time.process_time_ns()
    emit_trace(
        {
            "type": "agent.phase_start",
            "phase": phase,
            "session_key": session_key or "",
            "span_id": actual_span_id,
            "parent_span_id": actual_parent_span_id,
        }
    )
    success = False
    token = _CURRENT_TRACE_SPAN.set(actual_span_id)
    session_token = _CURRENT_TRACE_SESSION.set(session_key) if session_key is not None else None
    try:
        yield actual_span_id
        success = True
    finally:
        if session_token is not None:
            _CURRENT_TRACE_SESSION.reset(session_token)
        _CURRENT_TRACE_SPAN.reset(token)
        emit_trace(
            {
                "type": "agent.phase_end",
                "phase": phase,
                "session_key": session_key or "",
                "span_id": actual_span_id,
                "parent_span_id": actual_parent_span_id,
                "duration_ms": (time.monotonic_ns() - started_wall_ns) / 1_000_000,
                "cpu_ms": (time.process_time_ns() - started_cpu_ns) / 1_000_000,
                "success": success,
            }
        )


class TraceResourceSampler:
    """Optional process-resource sampler owned by the trace runtime."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        track_python_allocations: bool = False,
    ) -> None:
        self.interval_seconds = max(0.05, float(interval_seconds))
        self._started_ns = time.monotonic_ns()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any = None
        self._tracemalloc: Any = None
        self._owns_tracemalloc = False
        try:
            import psutil

            self._process = psutil.Process()
        except (ImportError, OSError):
            self._process = None
        if track_python_allocations:
            try:
                import tracemalloc

                self._tracemalloc = tracemalloc
                if not tracemalloc.is_tracing():
                    tracemalloc.start(1)
                    self._owns_tracemalloc = True
            except (ImportError, RuntimeError):
                self._tracemalloc = None

    def start(self) -> None:
        """启动唯一的后台采样线程；重复调用保持幂等。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="trace-resource-sampler",
        )
        self._thread.start()

    def _sample(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "perf.resource_sample",
            "process_cpu_ms": time.process_time_ns() / 1_000_000,
            "sampler_elapsed_ms": (time.monotonic_ns() - self._started_ns) / 1_000_000,
            "thread_count": threading.active_count(),
        }
        writer = _trace_writer
        if writer is not None:
            event["trace_queue_depth"] = writer.stats()["queue_depth"]
        process = self._process
        if process is not None:
            try:
                memory = process.memory_info()
                cpu = process.cpu_times()
                event.update(
                    {
                        "rss_bytes": int(memory.rss),
                        "cpu_user_ms": float(cpu.user) * 1000,
                        "cpu_system_ms": float(cpu.system) * 1000,
                    }
                )
            except (AttributeError, OSError):
                pass
        tracemalloc_module = self._tracemalloc
        if tracemalloc_module is not None:
            try:
                current, peak = tracemalloc_module.get_traced_memory()
                event.update(
                    {
                        "python_traced_bytes": int(current),
                        "python_traced_peak_bytes": int(peak),
                    }
                )
            except RuntimeError:
                pass
        return event

    def _run(self) -> None:
        emit_trace(self._sample())
        while not self._stop.wait(self.interval_seconds):
            emit_trace(self._sample())

    def shutdown(self) -> None:
        """请求采样线程停止，并在限定时间内等待退出。"""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval_seconds * 2))
        self._thread = None
        if self._owns_tracemalloc and self._tracemalloc is not None:
            try:
                self._tracemalloc.stop()
            except RuntimeError:
                pass
        self._owns_tracemalloc = False


class AsyncTraceWriter:
    """异步背景写入器，批处理 trace 事件。

    设计原理：
    - 主线程将事件推入有界队列（O(1)，非阻塞）
    - 后台线程批量写入文件（减少 I/O 次数）
    - 批处理间隔可配置（默认 100ms）
    - 批量大小可配置（默认 50 事件）
    - 优雅关闭机制尽量写完已入队数据
    - 背压保护：高频 trace 超过队列上限时丢弃事件并记录计数，避免内存无限增长
    - 进程隔离：每个进程写入独立文件（避免多进程冲突）

    性能优化：
    - 单事件延迟从 3-11ms 降到 <0.1ms
    - 文件 I/O 次数减少 50 倍
    """

    def __init__(
        self,
        batch_interval: float = 0.1,
        batch_size: int = 50,
        queue_max_size: int = 10000,
        overflow_policy: str = TRACE_OVERFLOW_DROP_OLDEST,
    ):
        """初始化异步写入器。

        Args:
            batch_interval: 批处理间隔（秒）
            batch_size: 批量大小（事件数）
            queue_max_size: 等待写入的最大事件数；小于等于 0 表示无界队列
            overflow_policy: 队列满时的策略，支持 ``drop_oldest`` / ``drop_newest``
        """
        self.batch_interval = max(0.001, float(batch_interval))
        self.batch_size = max(1, int(batch_size))
        self.queue_max_size = max(0, int(queue_max_size))
        if overflow_policy not in {TRACE_OVERFLOW_DROP_OLDEST, TRACE_OVERFLOW_DROP_NEWEST}:
            overflow_policy = TRACE_OVERFLOW_DROP_OLDEST
        self.overflow_policy = overflow_policy
        self._queue: queue.Queue[dict[str, Any] | _ExcludeSessionCommand | None] = queue.Queue(
            maxsize=self.queue_max_size
        )
        self._writer_thread: threading.Thread | None = None
        self._shutdown = False
        self._file_handle: Any = None
        self._file_path: Path | None = None
        self._base_file_path: Path | None = None
        self._initial_date: str | None = None
        self._active_date: str | None = None
        self._process_id = os.getpid()  # 进程ID（进程隔离）
        self._emitted_count = 0
        self._written_count = 0
        self._dropped_count = 0
        self._serialization_error_count = 0
        self._write_error_count = 0
        self._drop_warned = False
        self._serialization_warned = False
        self._excluded_sessions: set[str] = set()
        self._redacted_count = 0
        self._rotation_count = 0
        self._shutdown_incomplete = False
        self._enqueue_lock = threading.Lock()

    def start(self, file_path: Path) -> None:
        """启动后台写入线程。

        幂等保护：若已有打开的文件句柄/线程（重复调用 ``start``），先优雅关闭旧实例，
        避免文件描述符与线程泄漏。

        Args:
            file_path: trace 文件路径
        """
        # 幂等：重复 start 前回收旧句柄/线程，防止泄漏。
        if self._file_handle is not None or self._writer_thread is not None:
            self.shutdown()
            if self._writer_thread is not None:
                raise RuntimeError("trace writer cannot restart before its thread exits")

        # A successfully stopped writer may be reused by tests or an embedded
        # host. Recreate the FIFO so a consumed/queued shutdown sentinel can
        # never terminate the next lifecycle.
        self._shutdown = False
        self._shutdown_incomplete = False
        self._queue = queue.Queue(maxsize=self.queue_max_size)
        self._excluded_sessions.clear()

        self._base_file_path = Path(file_path)
        initial_match = _TRACE_DATE_RE.search(file_path.name)
        initial_date = (
            initial_match.group(1)
            if initial_match is not None
            else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        self._initial_date = initial_date
        self._open_for_date(initial_date, count_rotation=False)
        try:
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="trace-writer"
            )
            self._writer_thread.start()
        except Exception:
            try:
                self._file_handle.close()
            finally:
                self._file_handle = None
                self._writer_thread = None
            raise

    def _path_for_date(self, date: str) -> Path:
        """Return the process-isolated shard path for one UTC date."""
        base = self._base_file_path or Path("trace.jsonl")
        name = base.name
        if _TRACE_DATE_RE.search(name):
            name = _TRACE_DATE_RE.sub(f"trace-{date}", name, count=1)
        elif self._initial_date is not None and date != self._initial_date:
            plain_path = Path(name)
            name = f"{plain_path.stem}-{date}{plain_path.suffix}"
        stem_path = Path(name)
        if stem_path.suffix == ".jsonl":
            actual_name = f"{stem_path.stem}-pid{self._process_id}.jsonl"
        else:
            actual_name = f"{name}-pid{self._process_id}"
        return base.with_name(actual_name)

    def _open_for_date(self, date: str, *, count_rotation: bool = True) -> None:
        """Switch the sole writer handle to ``date`` without overlapping handles."""
        if self._active_date == date and self._file_handle is not None:
            return
        if self._file_handle is not None:
            self._file_handle.flush()
            self._file_handle.close()
            self._file_handle = None
        self._file_path = self._path_for_date(date)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_handle = self._file_path.open("a", encoding="utf-8")
        if self._active_date is not None and count_rotation:
            self._rotation_count += 1
        self._active_date = date

    @staticmethod
    def _event_date(event: dict[str, Any]) -> str:
        timestamp = event.get("ts")
        if isinstance(timestamp, str) and len(timestamp) >= 10:
            candidate = timestamp[:10]
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
                return candidate
            except ValueError:
                pass
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def emit(self, event: dict[str, Any]) -> None:
        """非阻塞发送事件（主线程调用）。

        Args:
            event: trace 事件字典
        """
        with self._enqueue_lock:
            if self._shutdown:
                return
            self._emitted_count += 1
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self._dropped_count += 1
                # 首次发生背压丢弃时记一次 warning（之后仅累计计数），避免静默数据丢失难以排查。
                if not self._drop_warned:
                    self._drop_warned = True
                    _logger.warning(
                        "Trace 队列已满，开始丢弃事件（queue_max=%s, policy=%s）；"
                        "可调高 trace.writer_queue_max_size 或降低事件量。后续丢弃仅累计 dropped_count。",
                        self.queue_max_size,
                        self.overflow_policy,
                    )
                if self.overflow_policy != TRACE_OVERFLOW_DROP_OLDEST:
                    return
                try:
                    oldest = self._queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    if isinstance(oldest, _ExcludeSessionCommand):
                        # Maintenance commands must never be sacrificed for a
                        # trace event. Requeue it and drop the newest event.
                        try:
                            self._queue.put_nowait(oldest)
                        except queue.Full:
                            pass
                        return
                try:
                    self._queue.put_nowait(event)
                except queue.Full:
                    pass

    def exclude_session(
        self,
        session_key: str,
        *,
        timeout: float = 5.0,
        reject_future: bool = True,
    ) -> int:
        """Remove one session from the active shard and reject future events.

        The command shares the event FIFO, so all events accepted before it are
        either filtered or persisted before the sole writer thread rewrites the
        file. No second thread mutates a file while its writer handle is active.
        """
        normalized = (session_key or "").strip()
        if not normalized or self._shutdown:
            return 0
        if reject_future:
            self._excluded_sessions.add(normalized)
        command = _ExcludeSessionCommand(normalized, reject_future=reject_future)
        with self._enqueue_lock:
            try:
                self._queue.put(command, timeout=max(0.01, timeout))
            except queue.Full:
                return 0
        command.done.wait(timeout=max(0.01, timeout))
        return command.removed

    def _writer_loop(self) -> None:
        """后台线程：批量写入循环。"""
        while not (self._shutdown and self._queue.empty()):
            try:
                batch = self._collect_writer_batch()
                if batch is None:
                    continue
                buffer, maintenance, stop_after_batch = batch
                self._write_trace_batch(buffer)
                self._apply_writer_maintenance(maintenance)
            except Exception as error:
                _logger.debug("Trace writer loop error: %s", error, exc_info=True)
            if stop_after_batch and self._queue.empty():
                break

    def _collect_writer_batch(
        self,
    ) -> tuple[list[tuple[str, str]], _ExcludeSessionCommand | None, bool] | None:
        """按时间/数量上限收集一批事件，关闭期间仅非阻塞排空。"""
        try:
            first = self._queue.get(timeout=self.batch_interval)
        except queue.Empty:
            return None
        buffer: list[tuple[str, str]] = []
        maintenance: _ExcludeSessionCommand | None = None
        stop = False

        def accept(item: Any) -> None:
            nonlocal maintenance, stop
            if item is None:
                stop = self._shutdown
            elif isinstance(item, _ExcludeSessionCommand):
                maintenance = item
            else:
                serialized = self._serialize_event(item)
                if serialized is not None:
                    buffer.append((self._event_date(item), serialized))

        accept(first)
        deadline = time.monotonic() + self.batch_interval
        while not stop and maintenance is None and len(buffer) < self.batch_size:
            try:
                item = (
                    self._queue.get_nowait()
                    if self._shutdown
                    else self._queue.get(timeout=max(0.0, deadline - time.monotonic()))
                )
            except queue.Empty:
                break
            accept(item)
        return buffer, maintenance, stop

    def _write_trace_batch(self, buffer: list[tuple[str, str]]) -> None:
        """按日期分片写入一批序列化事件并刷新句柄。"""
        if not buffer:
            return
        try:
            current_date: str | None = None
            lines: list[str] = []
            for event_date, line in buffer:
                if current_date is not None and event_date != current_date:
                    self._flush_trace_lines(current_date, lines)
                    lines = []
                current_date = event_date
                lines.append(line)
            if current_date is not None:
                self._flush_trace_lines(current_date, lines)
            self._written_count += len(buffer)
        except Exception as error:
            self._write_error_count += 1
            self._dropped_count += len(buffer)
            _logger.debug("Trace batch write failed: %s", error, exc_info=True)

    def _flush_trace_lines(self, event_date: str, lines: list[str]) -> None:
        """切换到指定日期分片并同步刷新一组行。"""
        self._open_for_date(event_date)
        assert self._file_handle is not None
        self._file_handle.writelines(lines)
        self._file_handle.flush()

    def _apply_writer_maintenance(self, command: _ExcludeSessionCommand | None) -> None:
        """在唯一 writer 线程内执行会话排除重写并唤醒等待方。"""
        if command is None:
            return
        command.removed = self._rewrite_without_session(command.session_key)
        command.done.set()

    def _serialize_event(self, event: dict[str, Any]) -> str | None:
        """Serialize one event compactly and account for malformed payloads."""
        if str(event.get("session_key") or "") in self._excluded_sessions:
            self._redacted_count += 1
            return None
        try:
            return (
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
        except (TypeError, ValueError) as error:
            self._serialization_error_count += 1
            self._dropped_count += 1
            if not self._serialization_warned:
                self._serialization_warned = True
                _logger.warning(
                    "Trace 事件无法 JSON 序列化，已丢弃；后续仅累计 serialization_error_count: %s",
                    type(error).__name__,
                )
            return None

    def _rewrite_without_session(self, session_key: str) -> int:
        """Stream-rewrite the active file while running on the writer thread."""
        file_path = self._file_path
        if file_path is None:
            return 0
        temp_path: Path | None = None
        removed = 0
        try:
            if self._file_handle is not None:
                self._file_handle.flush()
                self._file_handle.close()
                self._file_handle = None
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=file_path.parent,
                prefix=f".{file_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as target:
                temp_path = Path(target.name)
                if file_path.exists():
                    with file_path.open(encoding="utf-8") as source:
                        for line in source:
                            stripped = line.strip()
                            should_remove = False
                            if stripped:
                                try:
                                    parsed = json.loads(stripped)
                                    should_remove = (
                                        isinstance(parsed, dict)
                                        and parsed.get("session_key") == session_key
                                    )
                                except json.JSONDecodeError:
                                    pass
                            if should_remove:
                                removed += 1
                            else:
                                target.write(line if line.endswith("\n") else line + "\n")
            os.replace(temp_path, file_path)
            temp_path = None
            self._redacted_count += removed
            return removed
        except OSError as error:
            self._write_error_count += 1
            _logger.warning(
                "Trace 会话清理失败，保留原分片: %s",
                type(error).__name__,
            )
            return 0
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            try:
                self._file_handle = file_path.open("a", encoding="utf-8")
            except OSError as error:
                self._file_handle = None
                self._write_error_count += 1
                _logger.warning(
                    "Trace 分片重新打开失败: %s",
                    type(error).__name__,
                )

    def shutdown(self, timeout_seconds: float = 5.0) -> bool:
        """优雅关闭：等待队列清空。"""
        self._shutdown = True
        try:
            self._queue.put_nowait(None)  # 发送关闭信号
        except queue.Full:
            # _shutdown 已阻止新事件；writer 会自然排空满队列后退出，不能为了
            # 插入 sentinel 主动丢弃一条真实 trace。
            pass

        if self._writer_thread:
            self._writer_thread.join(timeout=max(0.01, float(timeout_seconds)))

        if self._writer_thread and self._writer_thread.is_alive():
            # Never close or rewrite a handle that the writer may still be
            # using.  The daemon remains responsible for draining it.
            self._shutdown_incomplete = True
            _logger.error("Trace writer did not stop before timeout; handle left open")
            return False

        # A maintenance command may have timed out while the queue was full;
        # shutdown is the final deterministic opportunity to redact it.
        for session_key in tuple(self._excluded_sessions):
            self._rewrite_without_session(session_key)

        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception as e:
                _logger.debug("Trace file close failed: %s", e)

        self._file_handle = None
        self._writer_thread = None
        self._shutdown_incomplete = False
        return True

    @property
    def file_path(self) -> Path | None:
        """实际写入的 pid 后缀 trace 文件路径。"""
        return self._file_path

    @property
    def dropped_count(self) -> int:
        """因队列背压被丢弃的事件数量。"""
        return self._dropped_count

    def stats(self) -> dict[str, Any]:
        """返回 writer 内部指标；不通过 trace 递归上报。"""
        return {
            "file_path": str(self._file_path) if self._file_path else None,
            "queue_depth": self._queue.qsize(),
            "queue_max_size": self.queue_max_size,
            "overflow_policy": self.overflow_policy,
            "emitted_count": self._emitted_count,
            "written_count": self._written_count,
            "dropped_count": self._dropped_count,
            "serialization_error_count": self._serialization_error_count,
            "write_error_count": self._write_error_count,
            "redacted_count": self._redacted_count,
            "rotation_count": self._rotation_count,
            "active_date": self._active_date,
            "excluded_session_count": len(self._excluded_sessions),
            "shutdown_incomplete": self._shutdown_incomplete,
            "shutdown": self._shutdown,
        }


def _sanitize_trace_event_for_persistence(event: dict[str, Any]) -> dict[str, Any]:
    """Return a metrics-only event for JSONL persistence.

    Process-local hooks still receive the original event. The persisted file is
    the long-lived artifact, so it must not store prompts, responses, tool args,
    API keys, or other bulky/sensitive payloads.
    """
    sanitized: dict[str, Any] = {}
    for key, value in event.items():
        key_lower = key.lower()
        if key_lower in _PERSISTENCE_DROP_KEYS:
            continue
        if key_lower in _PERSISTENCE_PREVIEW_KEYS:
            # Preview strings are useful to in-process hooks but can contain
            # credentials or user text.  Persist only their size.
            sanitized[f"{key}_chars"] = len(str(value))
            continue
        if isinstance(value, str):
            if key_lower in _PERSISTENCE_SAFE_STRING_KEYS:
                sanitized[key] = value[:256]
        elif key_lower in _PERSISTENCE_SAFE_SCALAR_KEYS and (
            isinstance(value, int | float | bool) or value is None
        ):
            sanitized[key] = value
        elif key_lower == "usage" and isinstance(value, dict):
            sanitized[key] = _sanitize_usage_metrics(value)
        elif isinstance(value, list):
            if key_lower in _PERSISTENCE_SAFE_STRING_LIST_KEYS and all(
                isinstance(item, str) for item in value
            ):
                sanitized[key] = [item[:64] for item in value[:20]]
    return sanitized


def _get_default_trace_file(config: TraceRuntimeConfig | None = None) -> Path:
    """获取默认的 trace 文件路径（基于日期）。

    文件命名：trace-YYYY-MM-DD.jsonl
    目录：workspaces/logs（或配置 trace.output_dir）

    Returns:
        今日 trace 文件路径
    """
    if config is None:
        from miniagent.agent.settings import get_config

        config = TraceRuntimeConfig.from_getter(get_config)
    output_dir = config.output_dir
    trace_dir = Path(output_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)

    # 日期命名
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return trace_dir / f"trace-{today}.jsonl"


def register_trace_hook(hook: TraceHook) -> None:
    """注册回调；同一进程内可多个、顺序调用。

    Args:
        hook: 接收单参 ``dict`` 的同步函数；异常不应向外抛出（由 ``emit_trace`` 吞掉）。
    """
    if hook not in _hooks:
        _hooks.append(hook)


def unregister_trace_hook(hook: TraceHook) -> None:
    """移除已注册的 trace 钩子（不存在时静默）。"""
    try:
        _hooks.remove(hook)
    except ValueError as e:
        _logger.debug("trace hook已移除: %s", e)


def clear_trace_hooks() -> None:
    """清空全部 trace 钩子（测试隔离或子进程重置用）。

    同时关闭异步写入器，清除 trace 日志文件配置和自动初始化标志，确保完全重置。
    """
    _hooks.clear()
    global \
        _TRACE_LOG_FILE, \
        _TRACE_RECORD_PAYLOAD, \
        _TRACE_SHUTDOWN_TIMEOUT_SECONDS, \
        _auto_initialized, \
        _resource_sampler, \
        _trace_writer

    if _resource_sampler is not None:
        _resource_sampler.shutdown()
        _resource_sampler = None

    # 关闭异步写入器
    if _trace_writer:
        stopped = _trace_writer.shutdown(_TRACE_SHUTDOWN_TIMEOUT_SECONDS)
        if stopped:
            _trace_writer = None

    if _trace_writer is not None:
        _auto_initialized = True
        return

    _TRACE_LOG_FILE = None
    _TRACE_RECORD_PAYLOAD = TRACE_RECORD_PAYLOAD_METRICS_ONLY
    _TRACE_SHUTDOWN_TIMEOUT_SECONDS = 5.0
    _auto_initialized = False


def auto_register_trace_file_hook(config: TraceRuntimeConfig | None = None) -> None:
    """自动注册 trace 文件持久化钩子（使用异步写入器）。

    启用条件（任一满足）：
    1. JSON 配置 debug.log_path 已设置
    2. JSON 配置 trace.enabled: true

    文件路径：
    1. debug.log_path 指定路径
    2. 默认路径（trace.output_dir/trace-YYYY-MM-DD.jsonl）

    在进程启动时调用一次（通常由 ``engine.init.init_subsystems`` 调用）。

    性能优化：
    - 使用异步写入器替代同步文件 hook
    - 批处理间隔 100ms，批量大小 50 事件
    - 非阻塞写入，消除 3-11ms 延迟

    示例配置：
        {"trace": {"enabled": true, "output_dir": "workspaces/logs"}}
    """
    global \
        _auto_initialized, \
        _TRACE_LOG_FILE, \
        _TRACE_RECORD_PAYLOAD, \
        _TRACE_SHUTDOWN_TIMEOUT_SECONDS, \
        _resource_sampler, \
        _trace_writer

    if _auto_initialized:
        return

    if config is None:
        from miniagent.agent.settings import get_config

        config = TraceRuntimeConfig.from_getter(get_config)

    log_path = config.debug_log_path
    target: Path | None = None
    if log_path:
        target = Path(log_path)
    elif config.enabled:
        target = _get_default_trace_file(config)

    if target is None:
        return

    writer = AsyncTraceWriter(
        batch_interval=config.writer_batch_interval,
        batch_size=config.writer_batch_size,
        queue_max_size=config.writer_queue_max_size,
        overflow_policy=config.writer_overflow_policy,
    )
    sampler: TraceResourceSampler | None = None
    try:
        writer.start(target)
        _trace_writer = writer
        if config.resource_sample_interval_seconds > 0:
            sampler = TraceResourceSampler(
                config.resource_sample_interval_seconds,
                track_python_allocations=config.track_python_allocations,
            )
            sampler.start()
    except Exception:
        if sampler is not None:
            sampler.shutdown()
        writer.shutdown(config.writer_shutdown_timeout_seconds)
        _trace_writer = None
        _resource_sampler = None
        _TRACE_LOG_FILE = None
        _auto_initialized = False
        raise

    _TRACE_LOG_FILE = target
    _TRACE_RECORD_PAYLOAD = config.record_payload
    _TRACE_SHUTDOWN_TIMEOUT_SECONDS = max(
        0.01, config.writer_shutdown_timeout_seconds
    )
    _resource_sampler = sampler
    _auto_initialized = True
    _logger.info(
        "Trace writer started: %s (actual=%s, batch_interval=%ss, batch_size=%d, queue_max=%s)",
        target,
        writer.file_path,
        config.writer_batch_interval,
        config.writer_batch_size,
        config.writer_queue_max_size,
    )


def get_actual_trace_file() -> Path | None:
    """获取当前进程实际写入的 trace 文件路径。"""
    if _trace_writer is not None:
        return _trace_writer.file_path
    return _TRACE_LOG_FILE


def get_trace_writer_stats() -> dict[str, Any] | None:
    """获取异步 trace writer 指标；未启用持久化时返回 None。"""
    if _trace_writer is None:
        return None
    return _trace_writer.stats()


def exclude_trace_session(session_key: str) -> tuple[Path | None, int]:
    """Redact one session from the active shard through the writer FIFO."""
    if _trace_writer is None:
        return None, 0
    return _trace_writer.file_path, _trace_writer.exclude_session(session_key)


def finalize_trace_session(session_key: str) -> tuple[Path | None, int]:
    """Remove a completed session without retaining a permanent tombstone."""
    if _trace_writer is None:
        return None, 0
    return _trace_writer.file_path, _trace_writer.exclude_session(
        session_key,
        reject_future=False,
    )


def emit_trace(event: dict[str, Any]) -> None:
    """派发事件；钩子异常不影响主流程。

    性能优化：
    - 钩子按注册顺序同步调用
    - 文件写入改为异步批处理（非阻塞）
    - 快速路径：无钩子且无写入器时直接返回

    Args:
        event: 结构化事件负载，通常为扁平 dict。
    """
    # 快速路径：无钩子且文件写入器未启用时直接返回
    if not _hooks and not _trace_writer:
        return

    inherited: dict[str, Any] = {}
    current_span = _CURRENT_TRACE_SPAN.get()
    current_session = _CURRENT_TRACE_SESSION.get()
    if current_span is not None and "parent_span_id" not in event:
        inherited["parent_span_id"] = current_span
    if current_session is not None and "session_key" not in event:
        inherited["session_key"] = current_session

    # 添加时间戳；调用方显式关联字段始终优先于上下文继承值。
    event_with_ts = {
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        **inherited,
        **event,
    }

    # 异步文件写入（非阻塞）
    if _trace_writer:
        if _TRACE_RECORD_PAYLOAD == TRACE_RECORD_PAYLOAD_METRICS_ONLY:
            _trace_writer.emit(_sanitize_trace_event_for_persistence(event_with_ts))
        else:
            _trace_writer.emit(event_with_ts)

    # 钩子按注册顺序同步调用
    for h in _hooks:  # 避免 list copy 开销
        try:
            h(event_with_ts)
        except Exception as e:
            _logger.debug("trace hook执行失败: %s", e)


def shutdown_trace_writer(timeout_seconds: float | None = None) -> dict[str, Any] | None:
    """关闭 trace 异步写入器（优雅退出）。

    应在进程退出前调用，确保所有 trace 事件都已写入文件。

    示例：
        # 在程序退出前调用
        from miniagent.agent.observability import shutdown_trace_writer
        shutdown_trace_writer()
    """
    global \
        _TRACE_LOG_FILE, \
        _auto_initialized, \
        _resource_sampler, \
        _trace_writer
    if _resource_sampler is not None:
        _resource_sampler.shutdown()
        _resource_sampler = None
    if _trace_writer is None:
        return None
    writer = _trace_writer
    writer.shutdown(
        _TRACE_SHUTDOWN_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    )
    stats = writer.stats()
    if stats["shutdown_incomplete"]:
        return stats
    _trace_writer = None
    _TRACE_LOG_FILE = None
    _auto_initialized = False
    _logger.info("Trace异步写入器已关闭")
    return stats


__all__ = [
    "TraceHook",
    "TraceResourceSampler",
    "TraceRuntimeConfig",
    "register_trace_hook",
    "unregister_trace_hook",
    "clear_trace_hooks",
    "emit_trace",
    "new_trace_id",
    "trace_parent",
    "trace_span",
    "llm_request_size_metrics",
    "auto_register_trace_file_hook",
    "get_actual_trace_file",
    "get_trace_writer_stats",
    "exclude_trace_session",
    "finalize_trace_session",
    "shutdown_trace_writer",  # 新增：关闭异步写入器
]
