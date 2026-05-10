"""轻量级 trace 钩子：供执行器发出结构化事件，可接入日志或外部 APM。

``emit_trace(event)`` 中的 ``event`` 建议为可 JSON 序列化的 ``dict``（至少含
``"kind"`` 或 ``"phase"`` 等区分字段）；具体键由调用方约定，钩子应容错未知字段。

进程内全局钩子列表；测试或子进程隔离场景可 ``clear_trace_hooks()``。
"""

from __future__ import annotations

from typing import Any, Callable

TraceHook = Callable[[dict[str, Any]], None]

_hooks: list[TraceHook] = []


def register_trace_hook(hook: TraceHook) -> None:
    """注册回调；同一进程内可多个、顺序调用。

    Args:
        hook: 接收单参 ``dict`` 的同步函数；异常不应向外抛出（由 ``emit_trace`` 吞掉）。
    """
    if hook not in _hooks:
        _hooks.append(hook)


def unregister_trace_hook(hook: TraceHook) -> None:
    try:
        _hooks.remove(hook)
    except ValueError:
        pass


def clear_trace_hooks() -> None:
    _hooks.clear()


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
]
