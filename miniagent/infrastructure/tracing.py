"""轻量级 trace 钩子：供执行器发出结构化事件，可接入日志或外部 APM。

``emit_trace(event)`` 中的 ``event`` 建议为可 JSON 序列化的 ``dict``（至少含
``"kind"`` 或 ``"phase"`` 等区分字段）；具体键由调用方约定，钩子应容错未知字段。

进程内全局钩子列表；测试或子进程隔离场景可 ``clear_trace_hooks()``。

**可选持久化**：在 JSON 配置中设置 ``trace.enabled: true`` 与 ``trace.output_dir``，自动注册钩子将事件写入 JSONL 文件（``workspaces/logs/trace-YYYY-MM-DD-pid{pid}.jsonl``）。

**事件类型规范**：见 ``miniagent.infrastructure.trace_events`` 模块。

**统计分析**：见 ``miniagent.infrastructure.trace_stats`` 模块。
"""

from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TraceHook = Callable[[dict[str, Any]], None]


class _ExcludeSessionCommand:
    """FIFO maintenance command processed by the sole writer thread."""

    __slots__ = ("done", "removed", "session_key")

    def __init__(self, session_key: str) -> None:
        self.session_key = session_key
        self.done = threading.Event()
        self.removed = 0


_hooks: list[TraceHook] = []

# 可选持久化配置
_TRACE_LOG_FILE: Path | None = None
_TRACE_RECORD_PAYLOAD = "metrics_only"

# 异步写入器实例
_trace_writer: AsyncTraceWriter | None = None

# 是否已自动初始化
_auto_initialized = False

# Logger
from miniagent.infrastructure.logger import get_logger

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
            self._shutdown = False

        # 进程隔离：添加进程ID后缀避免多进程写入冲突
        # 文件名格式：trace-YYYY-MM-DD-pid{process_id}.jsonl
        file_path_str = str(file_path)
        if ".jsonl" in file_path_str:
            # 在.jsonl之前插入pid后缀
            pid_suffix = f"-pid{self._process_id}"
            self._file_path = Path(file_path_str.replace(".jsonl", f"{pid_suffix}.jsonl"))
        else:
            # 其他情况：直接添加后缀
            self._file_path = Path(f"{file_path_str}-pid{self._process_id}")

        # 确保目录存在
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        # 打开文件（追加模式）；线程创建失败时回收句柄避免泄漏。
        self._file_handle = self._file_path.open("a", encoding="utf-8")
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

    def emit(self, event: dict[str, Any]) -> None:
        """非阻塞发送事件（主线程调用）。

        Args:
            event: trace 事件字典
        """
        if not self._shutdown:
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

    def exclude_session(self, session_key: str, *, timeout: float = 5.0) -> int:
        """Remove one session from the active shard and reject future events.

        The command shares the event FIFO, so all events accepted before it are
        either filtered or persisted before the sole writer thread rewrites the
        file. No second thread mutates a file while its writer handle is active.
        """
        normalized = (session_key or "").strip()
        if not normalized or self._shutdown:
            return 0
        self._excluded_sessions.add(normalized)
        command = _ExcludeSessionCommand(normalized)
        try:
            self._queue.put(command, timeout=max(0.01, timeout))
        except queue.Full:
            return 0
        command.done.wait(timeout=max(0.01, timeout))
        return command.removed

    def _writer_loop(self) -> None:
        """后台线程：批量写入循环。"""
        while not (self._shutdown and self._queue.empty()):
            buffer: list[str] = []
            stop_after_batch = False
            maintenance: _ExcludeSessionCommand | None = None
            try:
                # 无事件时最多每 batch_interval 唤醒一次；收到首事件后再按同一
                # interval 聚合后续事件，避免旧实现每 10ms 空轮询且低流量逐条 flush。
                try:
                    first = self._queue.get(timeout=self.batch_interval)
                except queue.Empty:
                    continue
                if first is None:
                    stop_after_batch = self._shutdown
                elif isinstance(first, _ExcludeSessionCommand):
                    maintenance = first
                else:
                    serialized = self._serialize_event(first)
                    if serialized is not None:
                        buffer.append(serialized)

                deadline = time.monotonic() + self.batch_interval
                while (
                    not stop_after_batch and maintenance is None and len(buffer) < self.batch_size
                ):
                    if self._shutdown:
                        # 关闭期间不会再接收新事件，因此只排空当前队列，不能继续
                        # 等待完整 batch_interval；否则大 interval 会令 shutdown
                        # 超过 join timeout 并在 writer 尚未退出时关闭文件句柄。
                        try:
                            event = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if event is None:
                            stop_after_batch = True
                        elif isinstance(event, _ExcludeSessionCommand):
                            maintenance = event
                        else:
                            serialized = self._serialize_event(event)
                            if serialized is not None:
                                buffer.append(serialized)
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        event = self._queue.get(timeout=remaining)
                        if event is None:
                            stop_after_batch = self._shutdown
                        elif isinstance(event, _ExcludeSessionCommand):
                            maintenance = event
                        else:
                            serialized = self._serialize_event(event)
                            if serialized is not None:
                                buffer.append(serialized)
                    except queue.Empty:
                        break

                if buffer and self._file_handle:
                    try:
                        self._file_handle.writelines(buffer)
                        self._file_handle.flush()
                        self._written_count += len(buffer)
                    except Exception as e:
                        self._write_error_count += 1
                        self._dropped_count += len(buffer)
                        _logger.debug("Trace batch write failed: %s", e)
                if maintenance is not None:
                    maintenance.removed = self._rewrite_without_session(maintenance.session_key)
                    maintenance.done.set()
            except Exception as e:
                _logger.debug("Trace writer loop error: %s", e)
            if stop_after_batch and self._queue.empty():
                break

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

    def shutdown(self) -> None:
        """优雅关闭：等待队列清空。"""
        self._shutdown = True
        try:
            self._queue.put_nowait(None)  # 发送关闭信号
        except queue.Full:
            # _shutdown 已阻止新事件；writer 会自然排空满队列后退出，不能为了
            # 插入 sentinel 主动丢弃一条真实 trace。
            pass

        if self._writer_thread:
            self._writer_thread.join(timeout=5.0)

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
            sanitized[key] = str(value)[:200]
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            sanitized[key] = value
        elif key_lower == "usage" and isinstance(value, dict):
            sanitized[key] = _sanitize_usage_metrics(value)
        elif isinstance(value, list) and all(
            isinstance(item, int | float | bool) for item in value
        ):
            # 仅保留纯数值/布尔数组（安全 metrics，如时延分布）；
            # 字符串列表可能含模型生成文本，按 metrics_only 策略丢弃。
            sanitized[key] = list(value[:50])
    return sanitized


def _get_default_trace_file() -> Path:
    """获取默认的 trace 文件路径（基于日期）。

    文件命名：trace-YYYY-MM-DD.jsonl
    目录：workspaces/logs（或配置 trace.output_dir）

    Returns:
        今日 trace 文件路径
    """
    from miniagent.infrastructure.json_config import get_config

    # 从配置获取目录
    output_dir = get_config("trace.output_dir", "workspaces/logs")
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
    global _TRACE_LOG_FILE, _TRACE_RECORD_PAYLOAD, _auto_initialized, _trace_writer

    # 关闭异步写入器
    if _trace_writer:
        _trace_writer.shutdown()
        _trace_writer = None

    _TRACE_LOG_FILE = None
    _TRACE_RECORD_PAYLOAD = TRACE_RECORD_PAYLOAD_METRICS_ONLY
    _auto_initialized = False


def auto_register_trace_file_hook() -> None:
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
    global _auto_initialized, _TRACE_LOG_FILE, _TRACE_RECORD_PAYLOAD, _trace_writer

    if _auto_initialized:
        return

    _auto_initialized = True

    from miniagent.infrastructure.json_config import get_config

    log_path = str(get_config("debug.log_path", "") or "").strip()
    if log_path:
        _TRACE_LOG_FILE = Path(log_path)
    elif get_config("trace.enabled", False):
        _TRACE_LOG_FILE = _get_default_trace_file()

    # 启动异步写入器（替代同步文件 hook）
    if _TRACE_LOG_FILE is not None:
        # 从配置读取批处理参数
        from miniagent.infrastructure.json_config import get_config

        batch_interval = get_config("trace.writer_batch_interval", 0.1)
        batch_size = get_config("trace.writer_batch_size", 50)
        queue_max_size = get_config("trace.writer_queue_max_size", 10000)
        overflow_policy = get_config("trace.writer_overflow_policy", TRACE_OVERFLOW_DROP_OLDEST)
        _TRACE_RECORD_PAYLOAD = get_config(
            "trace.record_payload", TRACE_RECORD_PAYLOAD_METRICS_ONLY
        )

        _trace_writer = AsyncTraceWriter(
            batch_interval=batch_interval,
            batch_size=batch_size,
            queue_max_size=queue_max_size,
            overflow_policy=overflow_policy,
        )
        _trace_writer.start(_TRACE_LOG_FILE)
        _logger.info(
            "Trace异步写入器已启动: %s (actual=%s, batch_interval=%ss, batch_size=%d, queue_max=%s)",
            _TRACE_LOG_FILE,
            _trace_writer.file_path,
            batch_interval,
            batch_size,
            queue_max_size,
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

    # 添加时间戳
    event_with_ts = {"ts": datetime.now(timezone.utc).isoformat(), **event}

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


def shutdown_trace_writer() -> dict[str, Any] | None:
    """关闭 trace 异步写入器（优雅退出）。

    应在进程退出前调用，确保所有 trace 事件都已写入文件。

    示例：
        # 在程序退出前调用
        from miniagent.infrastructure.tracing import shutdown_trace_writer
        shutdown_trace_writer()
    """
    global _trace_writer
    if _trace_writer is None:
        return None
    writer = _trace_writer
    writer.shutdown()
    stats = writer.stats()
    _trace_writer = None
    _logger.info("Trace异步写入器已关闭")
    return stats


__all__ = [
    "TraceHook",
    "register_trace_hook",
    "unregister_trace_hook",
    "clear_trace_hooks",
    "emit_trace",
    "llm_request_size_metrics",
    "auto_register_trace_file_hook",
    "get_actual_trace_file",
    "get_trace_writer_stats",
    "exclude_trace_session",
    "shutdown_trace_writer",  # 新增：关闭异步写入器
]
