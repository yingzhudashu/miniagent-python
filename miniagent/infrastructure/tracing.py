"""轻量级 trace 钩子：供执行器发出结构化事件，可接入日志或外部 APM。

``emit_trace(event)`` 中的 ``event`` 建议为可 JSON 序列化的 ``dict``（至少含
``"kind"`` 或 ``"phase"`` 等区分字段）；具体键由调用方约定，钩子应容错未知字段。

进程内全局钩子列表；测试或子进程隔离场景可 ``clear_trace_hooks()``。

**可选持久化**：设置环境变量 ``MINIAGENT_TRACE_LOG_FILE`` 后，自动注册钩子将事件写入 JSONL 文件。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TraceHook = Callable[[dict[str, Any]], None]

_hooks: list[TraceHook] = []

# 可选持久化配置
_TRACE_LOG_FILE: Path | None = None


def _init_trace_log_file() -> None:
    """初始化 trace 日志文件路径（从环境变量读取）。"""
    global _TRACE_LOG_FILE
    log_path = os.environ.get("MINIAGENT_TRACE_LOG_FILE", "").strip()
    if log_path:
        _TRACE_LOG_FILE = Path(log_path)
        # 确保目录存在
        _TRACE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _trace_file_hook(event: dict[str, Any]) -> None:
    """将 trace 事件写入 JSONL 文件的钩子。

    自动添加时间戳，异步写入失败时记录日志（不影响主流程）。

    Args:
        event: 结构化事件负载
    """
    if _TRACE_LOG_FILE is None:
        return
    try:
        # 添加时间戳
        event_with_ts = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        line = json.dumps(event_with_ts, ensure_ascii=False)
        with _TRACE_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # 钩子异常不应影响主流程，但应记录日志
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("Trace file hook write failed: %s", e)


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
    except ValueError:
        pass


def clear_trace_hooks() -> None:
    """清空全部 trace 钩子（测试隔离或子进程重置用）。

    同时清除 trace 日志文件配置，确保完全重置。
    """
    _hooks.clear()
    global _TRACE_LOG_FILE
    _TRACE_LOG_FILE = None


def auto_register_trace_file_hook() -> None:
    """自动注册 trace 文件持久化钩子（如果设置了环境变量）。

    在进程启动时调用一次，检查 ``MINIAGENT_TRACE_LOG_FILE`` 环境变量，
    如果设置则注册 ``_trace_file_hook`` 将事件写入 JSONL 文件。

    示例环境变量：
        MINIAGENT_TRACE_LOG_FILE=workspaces/logs/trace.jsonl
    """
    _init_trace_log_file()
    if _TRACE_LOG_FILE is not None and _trace_file_hook not in _hooks:
        register_trace_hook(_trace_file_hook)


def emit_trace(event: dict[str, Any]) -> None:
    """派发事件；钩子异常不影响主流程。

    Args:
        event: 结构化事件负载，通常为扁平 dict。
    """
    for h in list(_hooks):
        try:
            h(event)
        except Exception:
            pass


__all__ = [
    "TraceHook",
    "register_trace_hook",
    "unregister_trace_hook",
    "clear_trace_hooks",
    "emit_trace",
    "auto_register_trace_file_hook",
]
